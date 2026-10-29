"""Translation must survive idle/disconnect and OFF→ON on the same page."""
import asyncio
import time
import unittest
from translation import Translator


class MirrorFixture:
    width,height=720,1560
    clients={1}
    auxiliary_clients=0
    active_pointers=set()
    last_input_at=0
    input_generation=0
    latest_image=None
    captions=[]
    async def broadcast(self,state):pass
    async def release_pointers(self):self.active_pointers.clear()


class FeedFixture:
    ready=False
    seconds=.001
    def __init__(self,disconnect=False):self.calls=0;self.recoveries=0;self.disconnect=disconnect;self.fast_calls=[]
    async def start(self):self.ready=True
    async def snapshot(self,fast=False):
        self.calls+=1;self.fast_calls.append(fast)
        if self.disconnect and self.calls==2:raise OSError('Synthetic disconnect')
        return {'nodes':[{'id':'fixture/fixture-product-title','text':'Test bag',
                         'class':'android.widget.TextView','parent':-1,'box':[20,250,340,290]}]}
    async def recover(self,restart=False):self.recoveries+=1;self.ready=True
    async def close(self):self.ready=False


async def until(predicate,timeout=3):
    end=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>end:raise AssertionError('Expected translation state was not restored')
        await asyncio.sleep(.03)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_iphone_other_app_shows_original_pinduoduo_instruction(self):
        from unittest.mock import AsyncMock
        mirror=MirrorFixture();mirror.source_kind='iphone';mirror.foreground_pdd=False
        mirror.broadcast=AsyncMock()
        feed=FeedFixture()
        async def snapshot(fast=False):return {'nodes':[],'window':'outside-pinduoduo'}
        feed.snapshot=snapshot
        translator=Translator(mirror,feed)
        await translator.start()
        try:
            await until(lambda:any('обычный Pinduoduo' in call.args[0].get('translation_status','')
                                   for call in mirror.broadcast.call_args_list))
            self.assertEqual(mirror.captions,[])
        finally:await translator.close()

    async def test_disconnect_does_not_kill_translation_worker(self):
        mirror=MirrorFixture();feed=FeedFixture(disconnect=True);translator=Translator(mirror,feed)
        translator._request=lambda sources:{s:'Тестовая сумка' for s in sources}
        await translator.start()
        try:
            await until(lambda:feed.calls>=3 and translator.ready and bool(mirror.captions))
            self.assertEqual(translator.layout_failures,1)
            self.assertEqual(feed.recoveries,1)
            self.assertFalse(any(task.done() for task in translator.tasks))
        finally:await translator.close()

    async def test_switch_off_on_restores_same_title(self):
        mirror=MirrorFixture();feed=FeedFixture();translator=Translator(mirror,feed)
        translator._request=lambda sources:{s:'Тестовая сумка' for s in sources}
        await translator.start()
        try:
            await until(lambda:bool(mirror.captions))
            await translator.set_enabled(False)
            self.assertEqual(mirror.captions,[])
            await asyncio.sleep(.25)
            await translator.set_enabled(True)
            await until(lambda:bool(mirror.captions))
            self.assertEqual(mirror.captions[0]['role'],'title')
        finally:await translator.close()

    async def test_fast_layout_work_while_dragging(self):
        mirror=MirrorFixture();mirror.active_pointers={0};mirror.last_input_at=time.monotonic()
        feed=FeedFixture();translator=Translator(mirror,feed)
        translator._request=lambda sources:{s:'Тестовая сумка' for s in sources}
        await translator.start()
        try:
            await until(lambda:bool(mirror.captions))
            self.assertGreater(feed.calls,0)
            self.assertTrue(all(feed.fast_calls))
            mirror.active_pointers.clear()
            await until(lambda:bool(mirror.captions))
        finally:await translator.close()

    async def test_tree_race_preserves_visible_translation(self):
        from layout import LayoutChanging
        mirror=MirrorFixture();feed=FeedFixture();translator=Translator(mirror,feed)
        translator._request=lambda sources:{s:'Тестовая сумка' for s in sources}
        await translator.start()
        try:
            await until(lambda:bool(mirror.captions))
            async def changing(fast=False):raise LayoutChanging()
            feed.snapshot=changing
            await asyncio.sleep(.6)
            self.assertTrue(mirror.captions)
            self.assertEqual(feed.recoveries,0)
            self.assertEqual(translator.layout_failures,0)
        finally:await translator.close()

    async def test_fixed_notice_reaches_existing_iphone_without_model_call(self):
        mirror=MirrorFixture();feed=FeedFixture();translator=Translator(mirror,feed)
        async def snapshot(fast=False):
            return {'nodes':[{'id':'fixture/tv_content','text':'商品已售罄，推荐以下相似商品',
                    'class':'android.widget.TextView','parent':-1,'box':[100,209,380,237]}]}
        feed.snapshot=snapshot
        def forbidden(sources):raise AssertionError('Fixed UI notice must not invoke a translation model')
        translator._request=forbidden
        await translator.start()
        try:
            await until(lambda:bool(mirror.captions))
            self.assertEqual(mirror.captions[0]['text'],'Распродано. Похожие ниже')
            self.assertEqual(mirror.captions[0]['role'],'title')
            self.assertEqual(mirror.captions[0]['kind'],'notice')
            self.assertEqual(translator.page_notice,'sold_out')
            self.assertFalse(translator.cache)
        finally:await translator.close()


if __name__=='__main__':unittest.main()
