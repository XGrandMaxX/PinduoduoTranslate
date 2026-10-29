"""Synthetic motion and the standalone authenticated TLS phone connection."""
import asyncio
import hashlib
import json
import ssl
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from motion import ScrollMotion
from phone_link import PhoneLink
from controls import release


class MotionTests(unittest.TestCase):
    def test_scroll_tracks_cards_and_ignores_fixed_footer(self):
        rng=np.random.default_rng(4)
        page=rng.integers(0,255,(1500,480,3),dtype=np.uint8)
        tracker=ScrollMotion();tracker.region=(0,140,480,900)
        offset=0
        times=[]
        for y in range(0,180,6):
            image=page[y:y+1040].copy()
            image[:140]=page[:140];image[900:]=page[900:1040]
            start=time.perf_counter();dy,confidence=tracker.update(image);times.append(time.perf_counter()-start)
            if y:self.assertGreater(confidence,.8)
            offset+=dy
        self.assertAlmostEqual(offset,-174,delta=3)
        # Timing is measured separately after warm-up; a loaded CI host must
        # not turn this geometry regression into a flaky performance test.
        tracker.reset()
        self.assertEqual(tracker.update(image),(0.,0.))
        self.assertEqual(tracker.update(image),(0.,0.))


class FakeMirror:
    width,height=480,1040
    def __init__(self):
        self.clients=set();self.active_pointers=set();self.input_owner=None;self.events=[]
    async def subscribe(self):
        queue=asyncio.Queue(48);self.clients.add(queue)
        queue.put_nowait({'ready':True,'width':480,'height':1040,'captions':[]})
        queue.put_nowait(b'synthetic-video-packet')
        return queue
    async def touch(self,action,pointer,x,y):
        self.events.append((action,pointer,x,y))
        if action==0:self.active_pointers.add(pointer)
        if action in (1,3):self.active_pointers.discard(pointer)
    async def release_pointers(self):self.active_pointers.clear()


class LinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_pair_stream_touch_and_disconnect(self):
        mirror=FakeMirror()
        with tempfile.TemporaryDirectory() as directory:
            phone=PhoneLink(mirror,object(),Path(directory))
            with patch('phone_link.lan_address',return_value='127.0.0.1'):
                await phone.start()
            try:
                pair=phone.pair
                self.assertNotIn('secret',phone.state())
                self.assertTrue(phone.qr().startswith(b'\x89PNG'))
                context=ssl.create_default_context(cafile=str(Path(directory)/'tls-cert.pem'))
                reader,writer=await asyncio.open_connection('127.0.0.1',18890,ssl=context)
                certificate=writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
                self.assertEqual(hashlib.sha256(certificate).hexdigest(),pair['fingerprint'])
                writer.close();await writer.wait_closed()
                url='wss://127.0.0.1:18890/stream'
                with self.assertRaises(InvalidStatus):
                    async with connect(url,ssl=context):pass
                with self.assertRaises(InvalidStatus):
                    async with connect(url,ssl=context,origin='http://example.invalid',
                                       additional_headers={'Authorization':'Bearer '+pair['secret']}):pass
                async with connect(url,ssl=context,additional_headers={'Authorization':'Bearer '+pair['secret']}) as ws:
                    self.assertEqual(json.loads(await ws.recv())['width'],480)
                    self.assertEqual(await ws.recv(),b'synthetic-video-packet')
                    await ws.send(json.dumps({'type':'touch','action':0,'pointer':0,'x':120,'y':450}))
                    await asyncio.sleep(.03)
                    self.assertEqual(mirror.events,[(0,0,120,450)])
                    await release(mirror,'unrelated-observer')
                    self.assertEqual(mirror.active_pointers,{0})
                await asyncio.sleep(.05)
                self.assertFalse(mirror.active_pointers)
                self.assertFalse(mirror.clients)
            finally:await phone.stop()


if __name__=='__main__':unittest.main()
