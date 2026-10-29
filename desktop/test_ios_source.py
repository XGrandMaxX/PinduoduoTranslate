"""Synthetic iPhone transport, label selection and foreground isolation."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock,Mock
from unittest.mock import patch

import av
import numpy as np

from ios_mirror import IOSMirror,avcc_to_annexb,h264_config
from ios_layout import IOSLayoutFeed,select_label_nodes
from selection import select_targets
from translation import caption_lines


def polygon(x,y,right,bottom):
    return [[x,y],[right,y],[right,bottom],[x,bottom]]


class IOSSourceTests(unittest.TestCase):
    def test_touch_clock_uses_device_mach_ticks_not_host_nanoseconds(self):
        mirror=IOSMirror()
        mirror.hid_clock=(100000,1000000000,125,3)
        try:
            with patch('ios_mirror.time.monotonic_ns',return_value=1001000000):
                self.assertEqual(mirror.hid_timestamp(),124000)
        finally:mirror.pool.shutdown(wait=True)

    def test_wrapped_caption_keeps_badge_area_clear(self):
        boxes=[[110,600,450,621],[12,625,450,646],[12,650,240,671]]
        text='Тестовое название товара в семейной упаковке'
        lines=caption_lines(text,boxes)
        self.assertEqual(' '.join(t for t,_ in lines),text)
        self.assertEqual(lines[0][1][0],110)
        self.assertEqual(lines[1][1][0],12)

    def test_invalid_video_lengths_are_rejected(self):
        self.assertEqual(avcc_to_annexb(b'\0\0\0\2AB'),b'\0\0\0\1AB')
        for value in (b'\0\0\0\0',b'\0\0\0\5AB',b'\0'):
            with self.assertRaises(ValueError):avcc_to_annexb(value)

    def test_transcoded_h264_round_trip(self):
        mirror=IOSMirror()
        image=np.full((1808,832,3),[70,120,190],dtype=np.uint8)
        mirror.hevc=Mock()
        mirror.hevc.decode.return_value=[av.VideoFrame.from_ndarray(image,format='rgb24')]
        try:
            result=mirror._process(b'\0\0\0\0\x01\0\0\0\2AB',b'',True,123456)
            packets=result[0][0]
            self.assertTrue(packets[0][1])
            config=h264_config(packets[0][0])
            self.assertTrue(config)
            decoder=av.CodecContext.create('h264','r')
            frames=decoder.decode(av.Packet(config+packets[0][0]))
            self.assertEqual((frames[0].width,frames[0].height),(480,1044))
            actual=frames[0].to_ndarray(format='rgb24').mean(axis=(0,1))
            np.testing.assert_allclose(actual,[70,120,190],atol=5)
        finally:mirror.pool.shutdown(wait=True)

    def test_select_names_and_actions_excluding_photos_price_and_forms(self):
        image=np.full((1044,480,3),255,dtype=np.uint8)
        rng=np.random.default_rng(7)
        image[170:420]=rng.integers(0,220,(250,480,3),dtype=np.uint8)
        boxes=[polygon(12,435,220,456),polygon(250,435,465,456),
               polygon(10,250,220,270),polygon(10,480,220,501),
               polygon(10,515,220,536),polygon(280,980,430,1005)]
        texts=['Cotton summer shirt','Leather handbag 2026','Printed photo text',
               'Price: ¥39.90','Verification password','Buy Now']
        nodes=select_label_nodes(image,boxes,texts,[.99]*len(texts))
        selected=select_targets({'nodes':nodes},480,1044)
        self.assertEqual([v['source'] for v in selected],
                         ['Cotton summer shirt','Leather handbag 2026','Buy Now'])
        self.assertEqual(selected[-1]['translation'],'Купить')


class IOSPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_activates_background_pinduoduo_but_preserves_foreground_app(self):
        mirror=IOSMirror()
        app=Mock()
        app.__aenter__=AsyncMock(return_value=app)
        app.__aexit__=AsyncMock(return_value=False)
        app.launch_application=AsyncMock()
        try:
            with patch('pymobiledevice3.remote.core_device.app_service.AppServiceService',return_value=app):
                await mirror.open_pinduoduo()
                app.launch_application.assert_awaited_once_with('com.xunmeng.pinduoduo',kill_existing=True)
                app.launch_application.reset_mock()
                mirror.foreground_pdd=True
                await mirror.open_pinduoduo()
                app.launch_application.assert_not_awaited()
        finally:mirror.pool.shutdown(wait=True)

    async def test_static_video_bootstrap_does_not_wait_for_another_phone_frame(self):
        mirror=IOSMirror()
        mirror.config_packet=b'config'
        mirror.bootstrap=[b'key',b'delta']
        try:
            queue=await mirror.subscribe()
            self.assertIsInstance(queue.get_nowait(),dict)
            self.assertEqual([queue.get_nowait() for _ in range(3)],
                             [b'config',b'key',b'delta'])
        finally:mirror.pool.shutdown(wait=True)

    async def test_other_foreground_application_never_runs_ocr(self):
        mirror=Mock()
        feed=IOSLayoutFeed(mirror)
        feed.info=Mock()
        feed.info.proclist=AsyncMock(return_value=[{
            'bundleIdentifier':'fixture.other-app','foregroundRunning':True}])
        feed._recognize=Mock(side_effect=AssertionError('OCR must not run'))
        snapshot=await feed.snapshot()
        self.assertEqual(snapshot['nodes'],[])
        feed._recognize.assert_not_called()
        self.assertFalse(mirror.foreground_pdd)

    async def test_foreground_change_during_ocr_discards_recognized_labels(self):
        mirror=Mock()
        mirror.latest_image=np.zeros((1044,480,3),dtype=np.uint8)
        mirror.latest_image_at=time.monotonic()
        mirror.input_generation=mirror.navigation_generation=0
        mirror.scroll_offset=0
        mirror.last_navigation_at=0
        feed=IOSLayoutFeed(mirror)
        feed.info=Mock()
        feed.info.proclist=AsyncMock(side_effect=[[
            {'bundleIdentifier':'com.xunmeng.pinduoduo','foregroundRunning':True}],[]])
        feed._recognize=Mock(return_value=[{'text':'Synthetic product'}])
        snapshot=await feed.snapshot()
        self.assertEqual(snapshot['nodes'],[])


if __name__=='__main__':unittest.main()
