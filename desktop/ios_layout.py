"""Local OCR restricted to product-label panels and known shopping controls.

Unlike Android's native layout tree, CoreDevice doesn't expose a complete
element tree for this app. This conservative visual selector is intentionally
separate. No screenshots/text are saved or sent to an external provider.
"""
import asyncio
import contextlib
import re
import time

import numpy as np

from selection import BUTTONS, NOTICES, NO_TITLE

EXCLUDE = re.compile(
    r'free shipping|pay later|upfront|delivery|sold\b|sales?\b|reviews?|people joined|'
    r'people are buying|group buys?|joined|repeat customer|ending soon|no min|'
    r'authentic|bestseller|weekly|discount|cashback|share to|seller ships|auto.consolidate|'
    r'包邮|免运|好评|人已|人拼|已拼|已售|回头客|发货|退货|运费|优惠|验证码|密码|地址', re.I)


def select_label_nodes(image, boxes, texts, scores):
    """Conservative visual fallback, never translate text on busy image pixels."""
    height, width = image.shape[:2]
    white = (image.min(axis=2) > 227)
    color = image.max(axis=2).astype(np.int16)-image.min(axis=2)
    candidates, buttons = [], []
    for polygon, raw, score in zip(boxes, texts, scores):
        text = ' '.join(str(raw).split())
        x,y = map(int,np.floor(np.min(polygon,axis=0)))
        right,bottom = map(int,np.ceil(np.max(polygon,axis=0)))
        x,y,right,bottom = max(0,x),max(0,y),min(width,right),min(height,bottom)
        # OCR sometimes joins the blue shipping badge to the first title line.
        # Retain the title and start its rectangle after that badge.
        if re.match(r'^free shipping\s*',text,re.I):
            crop=image[y:bottom,x:right].astype(np.int16)
            blue=(crop[:,:,2]>150)&(crop[:,:,2]-crop[:,:,0]>60)&(crop[:,:,2]-crop[:,:,1]>25)
            columns=np.flatnonzero(blue.mean(axis=0)>.12) if crop.size else []
            if len(columns) and columns[-1]<crop.shape[1]*.65:
                x+=int(columns[-1])+3
                text=re.sub(r'^free shipping\s*','',text,flags=re.I).strip()
        w,h = right-x,bottom-y
        if score < .83 or not text or not 9 <= h <= height*.035 or w < 12:
            continue
        if y < height*.055 or bottom > height*.98:
            continue
        if text.casefold() in BUTTONS or text in NOTICES:
            buttons.append({'text':text,'box':[x,y,right,bottom],
                            'id':'ios-action','class':'Button','clickable':True})
            continue
        if not 11 <= len(text) <= 220 or NO_TITLE.search(text) or EXCLUDE.search(text):
            continue
        if re.search(r'[¥￥$€]|\d{7,}|\d+(?:\.\d+)?\s*%',text):
            continue
        if not re.search(r'[A-Za-z\u3400-\u9fff]',text) or y > height*.91:
            continue
        crop_white = white[y:bottom,x:right]
        if crop_white.mean() < .62 or (color[y:bottom,x:right] > 55).mean() > .06:
            continue
        # A label must sit below a sizeable photographic area in its own
        # column. Prices, chat text, address forms and plain dialogs fail this.
        if w < width*.58:
            left,edge = (0,width//2) if (x+right)/2 < width/2 else (width//2,width)
        else:
            left,edge = 0,width
        above = white[max(int(height*.08),y-int(height*.48)):max(0,y-12),left+5:edge-5]
        if above.shape[0] < 60:
            continue
        dense = (1-above.mean(axis=1)) > .45
        if len(dense) < 40 or np.convolve(dense.astype(float),np.ones(40),mode='valid').max() < 32:
            continue
        candidates.append({'text':text,'box':[x,y,right,bottom], 'column':(left,edge)})
    # Adjacent wrapped lines become one translation, within their original box.
    full_width=any(n['box'][2]-n['box'][0]>width*.6 for n in candidates)
    if full_width:
        for node in candidates:node['column']=(0,width)
    groups=[]
    for node in sorted(candidates,key=lambda n:(n['box'][1],n['box'][0])):
        x,y,r,b = node['box']
        previous = next((g for g in reversed(groups) if g['column']==node['column'] and
                         0 <= y-g['box'][3] <= 12 and g['lines'] < 3),None)
        if previous:
            previous['text'] += ' '+node['text']
            a=previous['box'];previous['box']=[min(a[0],x),a[1],max(a[2],r),b]
            previous['lines'] += 1
            previous['line_boxes'].append(node['box'])
        else:
            groups.append({**node,'lines':1,'line_boxes':[node['box']]})
    nodes=[]
    for group in groups[:1 if full_width else 4]:
        nodes.append({'text':group['text'],'box':group['box'], 'id':'product_title',
                      'class':'TextView','lines':group['lines'],
                      'line_boxes':group['line_boxes'],
                      'scroll_box':[0,int(height*.1),width,int(height*.91)]})
    return nodes + buttons[:20]


class IOSLayoutFeed:
    def __init__(self,mirror):
        self.mirror=mirror
        self.ready=False
        self.seconds=0.
        self.ocr=None
        self.provider=self.info=None
        self.last_data=None
        self.last_at=0.
        self.last_generation=-1
        self.last_scroll=0.

    async def start(self):
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.device_info import DeviceInfo
        self.provider=DvtProvider(self.mirror.rsd)
        await self.provider.__aenter__()
        self.info=DeviceInfo(self.provider)
        await self.info.__aenter__()
        # iPhone's Mach ticks are not Windows nanoseconds (A13: 125/3 ns).
        # Anchor reports to the device clock before continuous gestures.
        with contextlib.suppress(Exception):
            start=time.monotonic_ns()
            timing=await asyncio.wait_for(self.info.mach_time_info(),2)
            end=time.monotonic_ns()
            if len(timing)>=3 and timing[1]>0 and timing[2]>0:
                self.mirror.hid_clock=(int(timing[0]),(start+end)//2,int(timing[1]),int(timing[2]))
        self.ready=True

    def _recognize(self,image):
        if self.ocr is None:
            from rapidocr import RapidOCR
            self.ocr=RapidOCR(params={'Global.log_level':'critical','Global.use_cls':False,
                'Det.limit_side_len':480,'EngineConfig.onnxruntime.intra_op_num_threads':2,
                'EngineConfig.onnxruntime.inter_op_num_threads':1})
        result=self.ocr(image[:,:,::-1].copy())
        if result.boxes is None or not result.txts:
            return []
        return select_label_nodes(image,result.boxes,result.txts,result.scores)

    async def snapshot(self,fast=False):
        from layout import LayoutChanging
        started=time.monotonic()
        processes=await asyncio.wait_for(self.info.proclist(),2)
        is_pdd=any(p.get('bundleIdentifier')=='com.xunmeng.pinduoduo' and
                   p.get('foregroundRunning') is True for p in processes)
        self.mirror.foreground_pdd=is_pdd
        if not is_pdd:
            self.last_data=None
            return {'nodes':[],'window':'outside-pinduoduo'}
        # Keep cached captions moving with the video while a new local OCR
        # snapshot runs. Reuse only for small movements, within the same input.
        if (self.last_data and time.monotonic()-self.last_at < .7 and
            self.last_generation==self.mirror.input_generation and
            abs(self.mirror.scroll_offset-self.last_scroll)<3):
            return self.last_data
        image=self.mirror.latest_image
        if time.monotonic()-self.mirror.last_navigation_at<.35:
            raise LayoutChanging()
        # A static iPhone page can stop producing video frames. The existing
        # snapshot stays valid while the device service is connected.
        if image is None:
            raise LayoutChanging()
        generation=self.mirror.input_generation
        navigation=self.mirror.navigation_generation
        scroll=self.mirror.scroll_offset
        nodes=await asyncio.to_thread(self._recognize,image.copy())
        if navigation!=self.mirror.navigation_generation:
            raise LayoutChanging()
        delta=self.mirror.scroll_offset-scroll
        for node in nodes:
            if node.get('scroll_box'):
                node['box'][1]+=delta;node['box'][3]+=delta
                for box in node.get('line_boxes',[]):
                    box[1]+=delta;box[3]+=delta
        # Check foreground again before any recognized product text enters MT.
        processes=await asyncio.wait_for(self.info.proclist(),2)
        if not any(p.get('bundleIdentifier')=='com.xunmeng.pinduoduo' and
                   p.get('foregroundRunning') is True for p in processes):
            self.last_data=None
            return {'nodes':[],'window':'outside-pinduoduo'}
        self.seconds=round(time.monotonic()-started,4)
        self.last_at=time.monotonic()
        self.last_generation=generation
        self.last_scroll=scroll
        # Native navigation is unknown; reset visual anchors on a large scene
        # change rather than carrying labels over a different product page.
        self.last_data={'nodes':nodes,'window':f'iphone-pinduoduo-{navigation}'}
        return self.last_data

    async def recover(self,restart=False):
        await self.close()
        await self.start()

    async def close(self):
        self.ready=False
        self.last_data=None
        if self.info:
            with contextlib.suppress(Exception):await self.info.__aexit__(None,None,None)
        if self.provider:
            with contextlib.suppress(Exception):await self.provider.__aexit__(None,None,None)
        self.info=self.provider=None
