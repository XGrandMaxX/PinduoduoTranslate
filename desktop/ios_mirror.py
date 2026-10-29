"""USB CoreDevice video and HID. No audio, clipboard, recording or LAN relay.

Pinned pymobiledevice3 11.46.0: its native HEVC session is adapted to the
viewer's H264 protocol. Capture and encoding run in one dedicated thread.
"""
import asyncio
import contextlib
import functools
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
import uuid
import io

import av

from mirror import Mirror, CONFIG, KEYFRAME


def avcc_to_annexb(data):
    output = bytearray()
    offset = 0
    while offset + 4 <= len(data):
        size = int.from_bytes(data[offset:offset+4], 'big')
        offset += 4
        if not 0 < size <= len(data)-offset:
            raise ValueError('Invalid video NAL length')
        output.extend(b'\0\0\0\1' + data[offset:offset+size])
        offset += size
    if offset != len(data):
        raise ValueError('Incomplete video NAL')
    return bytes(output)


def h264_config(data):
    import re
    return b''.join(b'\0\0\0\1'+nal for nal in re.split(b'\x00\x00\x00?\x01', data)
                    if nal and nal[0] & 31 in (7, 8))


class IOSMirror(Mirror):
    source_kind = 'iphone'

    def __init__(self):
        super().__init__()
        self.width, self.height = 480, 1044
        self.rsd = self.source = None
        self.tasks = []
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='iphone-video')
        self.force_key = True
        self.encoder = None
        self.hevc = None
        self.reset_hevc = False
        self.capture_frames = 0
        self.capture_started = 0
        self.ios_input_error = ''
        self.last_touch = (0, 0)
        self.foreground_pdd = False
        self.navigation_generation=0
        self.last_navigation_at=0.
        self.touch_start=None
        self.touch_down_at=0.
        self.screenshot_provider=self.screenshot_service=None
        self.hid_clock=None

    async def start(self):
        # Suppress SDK tracebacks which may contain device identifiers/archives.
        import logging
        logging.getLogger('pymobiledevice3').setLevel(logging.CRITICAL)
        from pymobiledevice3.tunneld.api import get_tunneld_devices
        from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer, _SubState
        import pymobiledevice3.remote.core_device.screen_stream as module
        if not getattr(module.open_media_receiver, '_pinduo_fixed', False):
            receiver = functools.partial(module.open_media_receiver, bind_port=18899)
            receiver._pinduo_fixed = True
            module.open_media_receiver = receiver
        devices = await get_tunneld_devices(('127.0.0.1', 18897))
        if len(devices) != 1:
            for device in devices:
                await device.close()
            raise RuntimeError('Подключите один разблокированный iPhone по USB')
        self.rsd = devices[0]
        self.source = ScreenStreamServer(self.rsd, audio_default_on=False, motion_idr=False,rctl_enabled=True)
        # Stable private client identity lets Apple replace our stale session
        # after a terminated Windows process without stopping other clients.
        session_path=Path(__file__).resolve().parents[1]/'.audit/iphone-video-session.json'
        if session_path.exists():
            session_id=uuid.UUID(json.loads(session_path.read_text())['session'])
        else:
            session_id=uuid.uuid4()
            session_path.write_text(json.dumps({'session':str(session_id)}))
        self.source._shared_session_id=session_id
        self.queue = asyncio.Queue(48)
        self.source._subscribers[self.queue] = _SubState()
        self.hevc = av.CodecContext.create('hevc', 'r')
        self.hevc.thread_count = 2
        self.capture_started = time.monotonic()
        self.tasks.append(asyncio.create_task(self.source._keep_awake_loop()))
        try:
            await self._stop_owned_media()
            await asyncio.wait_for(self.source._ensure_fresh_stream(), 25)
            await self.source._ensure_hid()
            await asyncio.sleep(.6)  # iPhone 11 native HID authorization gate.
            self.control_ready = True
            self.session_count += 1
            self.tasks.append(asyncio.create_task(self._video_loop()))
            self.tasks.append(asyncio.create_task(self.source._decoder_refresh_loop()))
            self.tasks.append(asyncio.create_task(self.source._rctl_feedback_loop()))
        except Exception:
            await self.close()
            raise RuntimeError('Не удалось подключить экран iPhone. Разблокируйте телефон.') from None

    def state(self):
        return {**super().state(), 'source': 'iphone', 'phone_relay_available': False,
                'capture_frames': self.capture_frames, 'input_error': self.ios_input_error,
                'capture_queue':self.queue.qsize() if self.source else 0,
                'rtp_packets':getattr(self.source,'_rtp_packets_received',0),
                'rtp_frames':getattr(self.source,'_rtp_frames_received',0)}

    def _process(self, message, header, force_key, pts):
        fallback=isinstance(message,av.VideoFrame)
        if not fallback and (self.reset_hevc or message[4] == 2):
            self.hevc=av.CodecContext.create('hevc','r')
            self.hevc.thread_count=2
            self.reset_hevc=False
        frames = [message] if fallback else self.hevc.decode(av.Packet(header + avcc_to_annexb(message[5:])))
        output = []
        for frame in frames:
            frame = frame.reformat(width=self.width, height=self.height, format='yuv420p')
            if self.encoder is None:
                self.encoder = av.CodecContext.create('libx264', 'w')
                self.encoder.width, self.encoder.height = self.width, self.height
                self.encoder.pix_fmt = 'yuv420p'
                self.encoder.time_base = Fraction(1, 1000000)
                self.encoder.framerate = Fraction(60)
                self.encoder.gop_size = 60
                self.encoder.max_b_frames = 0
                self.encoder.thread_count = 2
                self.encoder.options = {'preset':'ultrafast','tune':'zerolatency','crf':'23',
                                        'profile':'baseline','x264-params':'repeat-headers=1:scenecut=0'}
                self.encoder.open()
            frame.pts = pts
            frame.time_base = Fraction(1, 1000000)
            if force_key:
                frame.pict_type = av.video.frame.PictureType.I
            packets = [(bytes(p), p.is_keyframe) for p in self.encoder.encode(frame)]
            self.capture_frames += 1
            image = frame.to_ndarray(format='rgb24') if self.capture_frames % 2 == 0 or self.latest_image is None else None
            if image is not None and time.monotonic()-self.last_input_at<1.5:
                motion=self.motion_tracker.update(image)
            else:
                motion=(0.,0.)
                self.motion_tracker.reset()
            output.append((packets, image, motion))
        return output

    async def _video_loop(self):
        for attempt in range(4):
            try:
                await self._video_session_loop()
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.ready=self.control_ready=False
                self.captions=[]
                self.latest_image=None
                self.error='Восстанавливаю видеоканал iPhone…'
                self.ios_input_error=type(error).__name__
                await self.broadcast(self.state())
                try:
                    await self._stop_owned_media()
                    await asyncio.wait_for(self.source._ensure_fresh_stream(force=True),25)
                    self.reset_hevc=True
                    self.force_key=True
                    self.motion_reset_pending=True
                    await self.source._ensure_hid()
                    await asyncio.sleep(.6)
                    self.control_ready=True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(2)
        self.error='Разблокируйте iPhone и перезапустите переводчик'
        await self.broadcast(self.state())

    async def _video_session_loop(self):
        try:
            while True:
                # CoreDevice may send no AUs on a completely static page.
                # That is not a disconnect: keep the last picture and HID.
                fallback=False
                try:message = await asyncio.wait_for(self.queue.get(),.25)
                except TimeoutError:
                    age=time.monotonic()-self.latest_image_at
                    moving=time.monotonic()-self.last_input_at<1.5
                    if self.ready and age<(0.25 if moving else 1.):continue
                    # One still frame also bootstraps an idle Apple display.
                    # Keep this read-only DVT fallback independent of HID/RTP.
                    message=await self._still_frame()
                    fallback=True
                key = not fallback and message[4] in (0, 2)
                header = b''.join(b'\0\0\0\1'+v for v in
                    (self.source._vps_nal,self.source._sps_nal,self.source._pps_nal) if v) if key else b''
                pts = int((time.monotonic()-self.capture_started)*1000000)
                force, self.force_key = self.force_key, False
                started = time.monotonic()
                result = await asyncio.get_running_loop().run_in_executor(
                    self.pool, self._process, message, header, force, pts)
                self.decode_seconds = round(time.monotonic()-started, 4)
                for packets, image, motion in result:
                    if image is not None:
                        self.latest_image = image
                        self.latest_image_at = time.monotonic()
                    self.frame_pts = pts
                    self.video_frames += 1
                    dy, confidence = motion
                    self.motion_confidence = confidence
                    if dy:
                        self.scroll_offset += dy
                        self.last_motion_at = time.monotonic()
                        await self.broadcast({'motion': {'offset':self.scroll_offset,'pts':pts}})
                    for data, is_key in packets:
                        if is_key:
                            self.bootstrap=[]
                            self.bootstrap_bytes=0
                            config = h264_config(data)
                            if config:
                                self.config_packet = struct.pack('>QI', CONFIG, len(config)) + config
                                self.config_count += 1
                                await self.broadcast(self.config_packet)
                        flags = pts | (KEYFRAME if is_key else 0)
                        self.packet_count += 1
                        packet=struct.pack('>QI',flags,len(data))+data
                        if is_key or self.bootstrap:
                            self.bootstrap.append(packet)
                            self.bootstrap_bytes+=len(packet)
                            if len(self.bootstrap)>65 or self.bootstrap_bytes>3*1024*1024:
                                self.bootstrap=[];self.force_key=True
                        await self.broadcast(packet)
                    if not self.ready:
                        self.ready = True
                        self.error = ''
                        await self.broadcast(self.state())
        except asyncio.CancelledError:
            raise

    async def _still_frame(self):
        if self.screenshot_service is None:
            from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
            from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
            self.screenshot_provider=DvtProvider(self.rsd)
            await self.screenshot_provider.__aenter__()
            self.screenshot_service=Screenshot(self.screenshot_provider)
            await self.screenshot_service.__aenter__()
        data=await asyncio.wait_for(self.screenshot_service.get_screenshot(),4)
        from PIL import Image
        return av.VideoFrame.from_image(Image.open(io.BytesIO(data)))

    async def _send_control(self, packet):
        if packet == bytes([17]):
            self.force_key = True

    async def subscribe(self):
        queue=asyncio.Queue(128)
        queue.put_nowait(self.state())
        if self.config_packet and self.bootstrap:
            queue.put_nowait(self.config_packet)
            for packet in self.bootstrap:queue.put_nowait(packet)
        self.clients.add(queue)
        self.force_key=True
        return queue

    async def touch(self, action, pointer, x, y):
        if not self.control_ready or pointer != 0:
            return
        x = min(65535, max(0, round(x / self.width * 65535)))
        y = min(65535, max(0, round(y / self.height * 65535)))
        async with self.control_lock:
            op = 'release' if action in (1,3) else 'contact'
            try:
                if action==1 and self.touch_down_at:
                    await asyncio.sleep(max(0,.05-(time.monotonic()-self.touch_down_at)))
                await self._touch_report(op,x,y)
                self.ios_input_error = ''
            except Exception as error:
                self.ios_input_error = type(error).__name__
                return
            self.last_touch = (x, y)
            if action == 0:
                self.active_pointers.add(0)
                self.touch_start=(x,y)
                self.touch_down_at=time.monotonic()
            elif action in (1,3):
                self.active_pointers.discard(0)
                if self.touch_start and abs(x-self.touch_start[0])+abs(y-self.touch_start[1])<500:
                    self.navigation_generation+=1
                    self.last_navigation_at=time.monotonic()
                self.touch_start=None
            self.last_input_at = time.monotonic()
            self.input_generation += 1

    async def release_pointers(self):
        if self.active_pointers and self.source:
            with contextlib.suppress(Exception):
                await self._touch_report('release',*self.last_touch)
        self.active_pointers.clear()

    def hid_timestamp(self):
        if not self.hid_clock:return None
        ticks,host_ns,numerator,denominator=self.hid_clock
        return (ticks+(time.monotonic_ns()-host_ns)*denominator//numerator)&((1<<48)-1)

    async def _touch_report(self,op,x,y):
        await self.source._ensure_hid()
        await self.source._uhs.send_touchscreen(0x02 if op=='release' else 0xC2,x,y,
                                               timestamp=self.hid_timestamp())

    async def scroll(self, x, y, amount):
        if self.active_pointers:
            return
        start = min(self.height*.82, max(self.height*.2, y))
        end = min(self.height*.85, max(self.height*.18, start+amount*self.height*.18))
        await self.touch(0,0,x,start)
        for step in range(1,13):
            await asyncio.sleep(.012)
            await self.touch(2,0,x,start+(end-start)*step/12)
        await self.touch(1,0,x,end)

    async def key(self, code):
        if not self.source or not self.control_ready:
            return
        if code == 4:  # Pinduoduo's own back arrow; its detail view disables edge-pop.
            await self.touch(0,0,self.width*.055,self.height*.08)
            await self.touch(1,0,self.width*.055,self.height*.08)
            return
        usage = {66:40,67:42,112:76,61:43,19:82,20:81,21:80,22:79}.get(code)
        if usage or code==204:
            try:
                if code==204:
                    await self.source._handle_key(b'{"usages":[224]}')
                    await asyncio.sleep(.05)
                await self.source._handle_key(json.dumps({'usages':[224,44] if code==204 else [usage]}).encode())
                await asyncio.sleep(.07)
                if code==204:
                    await self.source._handle_key(b'{"usages":[224]}')
                    await asyncio.sleep(.04)
                await self.source._handle_key(b'{"usages":[]}')
            except Exception as error:
                self.ios_input_error=type(error).__name__
                await self.broadcast(self.state())
                return
            if code==66:
                self.navigation_generation+=1
                self.last_navigation_at=time.monotonic()

    async def text(self, text):
        # Native virtual keyboard supports ASCII key events, not arbitrary
        # Unicode insertion. Never sync the phone clipboard to work around it.
        from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID
        if not self.control_ready:
            return
        if any(char not in ASCII_TO_HID for char in text):
            self.ios_input_error='Для русского/китайского ввода используйте клавиатуру iPhone'
            await self.broadcast(self.state())
            return
        for char in text[:200]:
            mapping = ASCII_TO_HID.get(char)
            usage, shift = mapping
            usages = [225,usage] if shift else [usage]
            try:
                await self.source._handle_key(json.dumps({'usages':usages}).encode())
                await self.source._handle_key(b'{"usages":[]}')
            except Exception as error:
                self.ios_input_error=type(error).__name__
                await self.broadcast(self.state())
                return
            await asyncio.sleep(.008)
        self.last_input_at = time.monotonic()
        self.input_generation += 1
        self.ios_input_error=''

    async def open_pinduoduo(self):
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        async with AppServiceService(self.rsd) as apps:
            await apps.launch_application('com.xunmeng.pinduoduo', kill_existing=False)

    async def _stop_owned_media(self):
        from pymobiledevice3.remote.core_device.display_service import DisplayService
        # Apple's stop-all also affects unrelated CoreDevice clients. Use it
        # only when every advertised session belongs to our private identity.
        async def stop():
            async with DisplayService(self.rsd) as service:
                status=await service.get_media_stream_server_status()
                sessions=status.get('sessions',[])
                def owned(session):
                    value=session.get('connection',{}).get('options',{}).get(
                        'avcMediaStreamOptionClientSessionID',{}).get('uuid')
                    return str(value)==str(self.source._shared_session_id)
                if sessions and all(owned(session) for session in sessions):
                    await service.stop_media_stream(stop_all=True)
        with contextlib.suppress(Exception):await asyncio.wait_for(stop(),4)

    async def close(self):
        self.closing = True
        await self.release_pointers()
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks.clear()
        if self.source:
            self.source._subscribers.clear()
            with contextlib.suppress(Exception):
                await self.source._stop_active_stream()
            with contextlib.suppress(Exception):
                await self.source._stop_hid()
            if self.source._hid_worker_task:
                self.source._hid_worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.source._hid_worker_task
            await self._stop_owned_media()
        if self.rsd:
            if self.screenshot_service:
                with contextlib.suppress(Exception):
                    await self.screenshot_service.__aexit__(None,None,None)
            if self.screenshot_provider:
                with contextlib.suppress(Exception):
                    await self.screenshot_provider.__aexit__(None,None,None)
            await self.rsd.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.ready = self.control_ready = False
