"""Android video/control transport. Targets only the dedicated local emulator."""
import asyncio
import contextlib
import secrets
import struct
import time
from pathlib import Path

import av

ROOT = Path(__file__).resolve().parents[1]
ADB = ROOT / '.validation-deps/android/platform-tools/adb.exe'
SERVER = ROOT / '.validation-deps/downloads/scrcpy-server-v4.1'
SERIAL = 'emulator-5554'
CONFIG = 1 << 62
KEYFRAME = 1 << 61
SESSION = 1 << 63
PTS_MASK = (1 << 61) - 1


class Mirror:
    def __init__(self):
        self.width, self.height = 720, 1560
        self.ready = False
        self.error = ''
        self.video_frames = 0
        self.latest_image = None
        self.latest_image_at = 0.0
        self.last_input_at = 0.0
        self.input_generation = 0
        self.captions = []
        self.clients = set()
        self.auxiliary_clients = 0
        self.control_writer = None
        self.control_reader = None
        self.control_worker = None
        self.control_ready = False
        self.recovery_worker = None
        self.closing = False
        self.video_writer = None
        self.control_lock = asyncio.Lock()
        self.worker = None
        self.process = None
        self.port = None
        self.decoder = self._decoder()
        self.decode_queue = asyncio.Queue(maxsize=8)
        self.decode_worker = None
        self.decode_config = None
        self.decode_needs_key = True
        self.decode_dropped = 0
        self.decode_seconds = 0.0
        from motion import ScrollMotion
        self.motion_tracker=ScrollMotion()
        self.motion_reset_pending=False
        self.scroll_offset=0.0
        self.motion_confidence=0.0
        self.last_motion_at=0.0
        self.frame_pts=0
        self.viewer_stats={}
        self.config_packet = None
        self.bootstrap = []
        self.bootstrap_bytes = 0
        self.bootstrap_has_key = False
        self.active_pointers = set()
        self.input_owner=None
        self.started_at = time.monotonic()
        self.packet_count = 0
        self.session_count = 0
        self.config_count = 0
        self.transport_log = None

    @staticmethod
    def _decoder():
        decoder = av.CodecContext.create('h264', 'r')
        decoder.thread_count = 2
        decoder.thread_type = 'SLICE'
        return decoder

    async def adb(self, *args):
        process = await asyncio.create_subprocess_exec(
            str(ADB), '-s', SERIAL, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, _ = await asyncio.wait_for(process.communicate(), 25)
        if process.returncode:
            raise RuntimeError('Не выполнена команда подключения к виртуальному Android')
        return output

    async def start(self):
        self.ready = False
        self.control_ready = False
        self.error = ''
        self.config_packet = None
        self.bootstrap = []
        self.bootstrap_bytes = 0
        self.bootstrap_has_key = False
        self.latest_image = None
        self.decoder = self._decoder()
        self.decode_config = None
        self.decode_needs_key = True
        while not self.decode_queue.empty():self.decode_queue.get_nowait()
        state = await self.adb('get-state')
        if state.strip() != b'device':
            raise RuntimeError('Виртуальный Android ещё не готов')
        name=await self.adb('emu','avd','name')
        if name.splitlines()[0].strip()!=b'PinduoLocal':
            raise RuntimeError('Этот Android не относится к переводчику Pinduoduo')
        await self.adb('push', str(SERVER), '/data/local/tmp/pinduo-screen-server.jar')
        session = f'{secrets.randbits(31):08x}'
        output = await self.adb('forward', 'tcp:0', f'localabstract:scrcpy_{session}')
        self.port = int(output.strip())
        command = (
            'CLASSPATH=/data/local/tmp/pinduo-screen-server.jar app_process / '
            f'com.genymobile.scrcpy.Server 4.1 scid={session} '
            'tunnel_forward=true audio=false control=true video_codec=h264 '
            'max_size=1040 max_fps=60 video_bit_rate=2200000 video_codec_options=i-frame-interval=1 '
            'send_device_meta=false send_frame_meta=true send_stream_meta=true '
            'cleanup=true keep_active=true stay_awake=true clipboard_autosync=false log_level=warn'
        )
        # scrcpy logs are intentionally not echoed: no screen/account data in logs.
        self.transport_log = (ROOT / '.audit/android-mirror-transport.log').open('ab')
        self.process = await asyncio.create_subprocess_exec(
            str(ADB), '-s', SERIAL, 'shell', command,
            stdout=self.transport_log, stderr=self.transport_log)
        video_reader = video_writer = None
        for _ in range(60):
            try:
                video_reader, video_writer = await asyncio.open_connection('127.0.0.1', self.port)
                dummy = await asyncio.wait_for(video_reader.readexactly(1), 1)
                if dummy == b'\0':
                    break
            except (OSError, EOFError, asyncio.IncompleteReadError, TimeoutError):
                if video_writer:
                    video_writer.close()
                await asyncio.sleep(.15)
        else:
            raise RuntimeError('Не запустился видеоканал Android')
        # StreamReaderProtocol holds a weak reference to its reader. Retain BOTH
        # halves; discarding the reader lets GC close a live control connection.
        self.control_reader, self.control_writer = await asyncio.open_connection('127.0.0.1', self.port)
        self.video_writer = video_writer
        codec = await asyncio.wait_for(video_reader.readexactly(4), 10)
        if codec != b'h264':
            raise RuntimeError('Неподдерживаемый видеокодек Android')
        self.worker = asyncio.create_task(self._read_video(video_reader, video_writer))
        self.decode_worker = asyncio.create_task(self._decode_loop())
        self.control_ready = True
        self.control_worker = asyncio.create_task(self._read_control())

    async def _read_control(self):
        try:
            while await self.control_reader.read(4096):
                pass  # No clipboard/ack payloads are retained or logged.
            await self._lost()
        except asyncio.CancelledError:
            raise
        except OSError:
            await self._lost()

    async def _lost(self):
        if self.closing or (self.recovery_worker and not self.recovery_worker.done()):
            return
        self.ready = self.control_ready = False
        self.latest_image = None
        self.captions = []
        self.active_pointers.clear()
        self.error = 'Переподключаю экран и управление…'
        await self.broadcast(self.state())
        self.recovery_worker = asyncio.create_task(self._recover())

    async def _recover(self):
        for attempt in range(5):
            await self._stop_transport()
            await asyncio.sleep(min(1+attempt, 5))
            try:
                await self.start()
                await self.broadcast(self.state())
                return
            except (OSError, RuntimeError, TimeoutError, asyncio.IncompleteReadError):
                self.error = 'Не удалось переподключиться к виртуальному телефону'
        await self.broadcast(self.state())

    def state(self):
        return {'ready': self.ready, 'control_ready': self.control_ready, 'width': self.width, 'height': self.height,
                'error': self.error, 'video_frames': self.video_frames,
                'packets': self.packet_count, 'sessions': self.session_count, 'configs': self.config_count,
                'decode_dropped':self.decode_dropped,'decode_seconds':self.decode_seconds,
                  'captions': self.captions, 'scroll_offset':self.scroll_offset,
                  'motion_confidence':self.motion_confidence, 'translation_ready': False}

    async def _read_video(self, reader, writer):
        try:
            while True:
                header = await reader.readexactly(12)
                self.packet_count += 1
                flags, size = struct.unpack('>QI', header)
                if flags & SESSION:
                    self.session_count += 1
                    _, self.width, self.height = struct.unpack('>III', header)
                    self.decode_needs_key = True
                    self.motion_reset_pending=True
                    self.scroll_offset=0
                    self.bootstrap = []
                    self.bootstrap_bytes = 0
                    self.bootstrap_has_key = False
                    self.config_packet = None
                    await self.broadcast(self.state())
                    continue
                if not 0 < size <= 8 * 1024 * 1024:
                    raise RuntimeError('Некорректный размер видеопакета')
                data = await reader.readexactly(size)
                packet = header + data
                if flags & CONFIG:
                    self.config_count += 1
                    self.config_packet = packet
                    self.decode_config = data
                    self.decode_needs_key = True
                    while not self.decode_queue.empty():self.decode_queue.get_nowait()
                elif flags & KEYFRAME:
                    self.bootstrap = []
                    self.bootstrap_bytes = 0
                    self.bootstrap_has_key = True
                if not flags & CONFIG and self.bootstrap_has_key:
                    self.bootstrap.append(packet)
                    self.bootstrap_bytes += len(packet)
                    if self.bootstrap_bytes > 5 * 1024 * 1024 or len(self.bootstrap) > 120:
                        self.bootstrap = []
                        self.bootstrap_bytes = 0
                        self.bootstrap_has_key = False
                # Sending video never waits for the translation snapshot decoder.
                await self.broadcast(packet)
                if not flags & CONFIG:
                    if not self.ready:
                        self.ready=True
                        await self.broadcast(self.state())
                    self._offer_decode(flags,data)
        except asyncio.CancelledError:
            raise
        except (EOFError, asyncio.IncompleteReadError, OSError, RuntimeError, av.error.FFmpegError):
            await self._lost()
        finally:
            writer.close()

    def _decode(self, data):
        return [frame.to_ndarray(format='rgb24') for frame in self.decoder.decode(av.Packet(data))]

    def _offer_decode(self,flags,data):
        if self.decode_queue.full():
            while not self.decode_queue.empty():self.decode_queue.get_nowait()
            self.decode_needs_key=True
            self.decode_dropped+=1
        if self.decode_needs_key and not flags & KEYFRAME:return
        reset=self.decode_config if self.decode_needs_key else None
        self.decode_needs_key=False
        self.decode_queue.put_nowait((self.session_count,reset,data,flags & PTS_MASK))

    def _decode_item(self,reset,data):
        if self.motion_reset_pending or reset:
            self.motion_tracker.reset()
            self.motion_reset_pending=False
        if reset:
            self.decoder=self._decoder()
            self.decoder.extradata=reset
        images=self._decode(data)
        motion=self.motion_tracker.update(images[-1]) if images else (0.,0.)
        return images,motion

    async def _decode_loop(self):
        while True:
            generation,reset,data,pts=await self.decode_queue.get()
            started=time.monotonic()
            try:
                images,motion=await asyncio.to_thread(self._decode_item,reset,data)
            except av.error.FFmpegError:
                self.decode_needs_key=True
                continue
            self.decode_seconds=round(time.monotonic()-started,4)
            if images and generation==self.session_count:
                self.latest_image=images[-1]
                self.latest_image_at=time.monotonic()
                self.video_frames+=len(images)
                self.frame_pts=pts
                dy,confidence=motion
                self.motion_confidence=confidence
                if dy:
                    self.scroll_offset+=dy
                    self.last_motion_at=time.monotonic()
                    await self.broadcast({'motion':{'offset':self.scroll_offset,'pts':pts}})

    async def broadcast(self, message):
        for queue in tuple(self.clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Reconnect from a complete keyframe instead of accumulating delay.
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    async def subscribe(self):
        queue = asyncio.Queue(maxsize=48)
        queue.put_nowait(self.state())
        self.clients.add(queue)
        # Request a current keyframe instead of replaying several seconds of GOP.
        await self._send_control(bytes([17]))
        return queue

    async def touch(self, action, pointer, x, y):
        if action not in (0, 1, 2, 3) or not 0 <= pointer <= 9:
            return
        x = min(max(int(x), 0), self.width - 1)
        y = min(max(int(y), 0), self.height - 1)
        if action == 0:
            self.active_pointers.add(pointer)
        elif action in (1, 3):
            self.active_pointers.discard(pointer)
        packet = struct.pack('>BBQIIHHHII', 2, action, pointer, x, y,
                             self.width, self.height, 0 if action in (1, 3) else 65535, 0, 0)
        await self._send_control(packet)
        self.last_input_at = time.monotonic()
        self.input_generation += 1

    async def key(self, code):
        if code not in (3, 4, 19, 20, 21, 22, 61, 67, 66, 112, 187):
            return
        for action in (0, 1):
            await self._send_control(struct.pack('>BBIII', 0, action, code, 0, 0))
        self.last_input_at = time.monotonic()
        self.input_generation += 1

    async def text(self, text):
        data = text.encode('utf-8')[:4096]
        await self._send_control(bytes([1]) + struct.pack('>I', len(data)) + data)
        self.last_input_at = time.monotonic()
        self.input_generation += 1

    async def scroll(self,x,y,amount):
        x=min(max(int(x),0),self.width-1)
        y=min(max(int(y),0),self.height-1)
        axis=min(32767,max(-32768,round(amount*2048)))
        await self._send_control(struct.pack('>BIIHHhhI',3,x,y,self.width,self.height,0,axis,0))
        self.last_input_at=time.monotonic()
        self.input_generation+=1

    async def release_pointers(self):
        for pointer in tuple(self.active_pointers):
            await self.touch(3, pointer, self.width // 2, self.height // 2)

    async def _send_control(self, packet):
        async with self.control_lock:
            if self.control_writer is not None and self.control_ready:
                self.control_writer.write(packet)
                try:
                    await self.control_writer.drain()
                except OSError:
                    await self._lost()
                    raise

    async def close(self):
        self.closing = True
        if self.recovery_worker and not self.recovery_worker.done():
            self.recovery_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.recovery_worker
        with contextlib.suppress(OSError):
            await self.release_pointers()
        await self._stop_transport()

    async def _stop_transport(self):
        for task in (self.worker, self.control_worker,self.decode_worker):
            if not task or task is asyncio.current_task():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for writer in (self.control_writer, self.video_writer):
            if writer:
                writer.close()
        self.control_ready = False
        self.control_reader = self.control_writer = self.video_writer = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.process.wait(), 5)
        if self.port:
            with contextlib.suppress(Exception):
                await self.adb('forward', '--remove', f'tcp:{self.port}')
        if self.transport_log:
            self.transport_log.close()
