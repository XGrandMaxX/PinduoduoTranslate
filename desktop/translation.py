"""Local OCR and Russian captions. Images, recognized text and cache stay in RAM."""
import asyncio
import contextlib
import json
import re
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CHINESE = re.compile(r'[\u3400-\u9fff]')
NUMBER = re.compile(r'\d+(?:[.,]\d+)?')


def fits_image(image,box):
    x,y,w,h=box
    return x>=0 and y>=0 and w>0 and h>0 and x+w<=image.shape[1] and y+h<=image.shape[0]


def signature(image, box):
    x, y, w, h = box
    crop = image[y:y+h, x:x+w]
    return np.asarray(Image.fromarray(crop).convert('L').resize((48, 12)), dtype=np.int16)


def unchanged(image, anchor):
    x, y, w, h = anchor['pixels']
    if x < 0 or y < 0 or x+w > image.shape[1] or y+h > image.shape[0]:
        return False
    difference = np.abs(signature(image, anchor['pixels']) - anchor['signature'])
    return float(difference.mean()) < 9 and float((difference > 35).mean()) < .12


def valid_translation(source, target):
    # A model cannot change a price/quantity, return a paragraph, or execute an instruction.
    if not target or len(target) > max(50, len(source)*9) or CHINESE.search(target):
        return False
    if not re.search(r'[А-Яа-яЁё]', target):
        return False
    for original,required in ((r'\bcotton\b|纯棉','хлоп'),(r'\bsilk\b|真丝','шел'),
                              (r'\bwool\b|羊毛','шерст'),(r'\bleather\b|真皮','кож')):
        if re.search(original,source,re.I) and required not in target.lower().replace('ё','е'):
            return False
    return [v.replace(',', '.') for v in NUMBER.findall(source)] == [v.replace(',', '.') for v in NUMBER.findall(target)]


class Translator:
    def __init__(self, mirror, feed=None):
        from layout import LayoutFeed
        self.mirror = mirror
        self.feed = feed or LayoutFeed(mirror)
        self.enabled = True
        self.ready = False
        self.status = 'Подключаю названия и кнопки'
        self.anchors = []
        self.cache = OrderedDict()
        self.retry_after = {}
        self.wakeup = asyncio.Event()
        self.tasks = []
        self.layout_seconds = 0.0
        self.translation_seconds = 0.0
        self.anchor_generation = -1
        self.last_layout_at = 0.0
        self.selected_titles = self.selected_buttons = 0
        self.english_mt = None
        self.layout_failures = 0
        self.last_layout_error = ''
        self.window_id = None
        self.page_notice = None

    async def start(self):
        self.tasks = [asyncio.create_task(self._recognize()), asyncio.create_task(self._translate()),
                      asyncio.create_task(self._publish())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.feed.close()

    async def set_enabled(self, enabled):
        self.enabled = enabled
        self.anchors = []
        self.mirror.captions = []
        self.status = 'Названия и кнопки' if enabled else 'Перевод выключен'
        await self.mirror.broadcast({'captions': [], 'translation_status': self.status,
                                     'translation_enabled':enabled})

    async def _recognize(self):
        from selection import select_targets
        from layout import LayoutChanging
        previous=None
        idle=0
        consecutive_failures=0
        while True:
            try:
                if not self.feed.ready:
                    await self.feed.start()
                    self.ready=True
                moving=bool(self.mirror.active_pointers) or time.monotonic()-self.mirror.last_input_at<.5 or time.monotonic()-getattr(self.mirror,'last_motion_at',0)<.3
                await asyncio.sleep(.18 if moving or idle<4 else .5)
                mirror=self.mirror
                if not self.enabled or not (mirror.clients or mirror.auxiliary_clients):continue
                if mirror.active_pointers and time.monotonic()-mirror.last_input_at>5:
                    await mirror.release_pointers()
                generation = mirror.input_generation
                try:
                    snapshot = await self.feed.snapshot(fast=moving)
                    self.ready=True
                    self.last_layout_error=''
                    consecutive_failures=0
                except LayoutChanging:
                    # Keep the moving captions through a transient native
                    # traversal race; reconnecting here caused visible gaps.
                    continue
                except (OSError,ValueError,TimeoutError):
                    self.ready=False
                    self.anchors=[]
                    self.selected_titles=self.selected_buttons=0
                    self.layout_failures+=1
                    consecutive_failures+=1
                    self.last_layout_error='layout_connection'
                    self.status='Восстанавливаю перевод…'
                    await self.feed.recover(restart=consecutive_failures>=2)
                    await asyncio.sleep(.3)
                    continue
                if not self.enabled:
                    idle = 0
                    continue
                targets = select_targets(snapshot,mirror.width,mirror.height)
                window=snapshot.get('window')
                if window is not None and self.window_id is not None and window!=self.window_id:
                    self.anchors=[];previous=None
                    mirror.scroll_offset=0
                    mirror.motion_reset_pending=True
                if window is not None:self.window_id=window
                if snapshot.get('fast'):
                    targets.extend(a for a in self.anchors if a['role']=='button' and not a.get('scroll_box'))
                    targets.extend(a for a in self.anchors if a['role']=='notice')
                    if not any(a['role']=='title' for a in targets):
                        targets.extend(a for a in self.anchors if a['role']=='title')
                key = (getattr(mirror,'session_count',0),window,tuple((a['source'],a['pixels'],a['role']) for a in targets))
                self.last_layout_at = time.monotonic()
                self.layout_seconds = self.feed.seconds
                self.selected_titles = sum(a['role']=='title' for a in targets)
                self.selected_buttons = sum(a['role']=='button' for a in targets)
                notices=[a.get('notice_code') for a in targets if a['role']=='notice']
                self.page_notice=next((code for code in notices if code!='retry_later'),notices[0] if notices else None)
                if key == previous and self.anchors:
                    if mirror.latest_image is not None:
                        for anchor in self.anchors:
                            if not fits_image(mirror.latest_image,anchor['pixels']):continue
                            if 'signature' not in anchor or not unchanged(mirror.latest_image,anchor):
                                anchor['signature']=signature(mirror.latest_image,anchor['pixels'])
                    if any(a['role']=='title' and a['source'] not in self.cache and
                           self.retry_after.get(a['source'],0)<time.monotonic() for a in self.anchors):
                        self.wakeup.set()
                    idle += 1
                    continue
                idle = 0
                previous = key
                image = mirror.latest_image
                for anchor in targets:
                    anchor.setdefault('scroll_ref',getattr(mirror,'scroll_offset',0))
                    if image is not None:
                        if not fits_image(image,anchor['pixels']):continue
                        anchor['signature'] = signature(image,anchor['pixels'])
                        x,y,w,h = anchor['pixels']
                        crop=image[y:y+h,x:x+w]
                        edge=np.concatenate((crop[:1].reshape(-1,3),crop[-1:].reshape(-1,3)),axis=0)
                        rgb=np.median(edge,axis=0).astype(int)
                        anchor['background']='#'+''.join(f'{v:02x}' for v in rgb)
                    else:
                        anchor['background']='#ffffff'
                self.anchors = targets
                regions=[a['scroll_box'] for a in targets if a.get('scroll_box')]
                if regions and hasattr(mirror,'motion_tracker'):mirror.motion_tracker.region=regions[0]
                self.anchor_generation = generation
                self.status = 'Названия и кнопки'
                self.wakeup.set()
            except asyncio.CancelledError:raise
            except Exception as error:
                self.ready=False
                self.anchors=[]
                self.layout_failures+=1
                self.last_layout_error=type(error).__name__
                self.status='Восстанавливаю перевод…'
                with contextlib.suppress(Exception):await self.feed.recover(restart=True)
                await asyncio.sleep(1)

    def _request(self, sources):
        english=[source for source in sources if not CHINESE.search(source)]
        chinese=[source for source in sources if CHINESE.search(source)]
        translated={}
        if english:
            if self.english_mt is None:
                from english_mt import EnglishMT
                self.english_mt=EnglishMT()
            translated.update({s:t for s,t in self.english_mt.translate(english).items() if valid_translation(s,t)})
        if not chinese:return translated
        translated.update(self._request_chinese(chinese))
        return translated

    def _request_chinese(self, sources):
        numbered = '\n'.join(f'{i+1}. {source}' for i, source in enumerate(sources))
        prompt = ('将以下文本翻译为俄语，注意只需要输出翻译后的结果，不要额外解释。保留每行编号、品牌、型号和数字：\n\n' + numbered)
        payload = {'messages': [{'role': 'user', 'content': prompt}], 'temperature': .2,
                   'top_k': 20, 'top_p': .6, 'repeat_penalty': 1.05, 'max_tokens': 240}
        key = (ROOT / '.audit/translation-local.key').read_text().strip()
        request = urllib.request.Request('http://127.0.0.1:18891/v1/chat/completions',
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json',
                                                       'Authorization': 'Bearer '+key})
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = json.load(response)['choices'][0]['message']['content']
        pairs = {}
        for line in raw.splitlines():
            match = re.match(r'^\s*(\d+)[.)、]\s*(.+?)\s*$', line)
            if match:
                index = int(match[1])-1
                if 0 <= index < len(sources) and index not in pairs:
                    target = match[2].strip()
                    if valid_translation(sources[index], target):
                        pairs[index] = target
        return {sources[i]: target for i, target in pairs.items()}

    async def _translate(self):
        while True:
            await self.wakeup.wait()
            self.wakeup.clear()
            if not self.enabled:continue
            now=time.monotonic()
            sources = list(dict.fromkeys(a['source'] for a in self.anchors
                if a['role']=='title' and a['source'] not in self.cache and
                self.retry_after.get(a['source'],0)<now))[:4]
            if not sources:continue
            started = time.monotonic()
            try:
                translated = await asyncio.to_thread(self._request, sources)
                self.translation_seconds = round(time.monotonic()-started, 3)
                for source in sources:
                    if source in translated:
                        self.cache[source]=translated[source]
                        self.cache.move_to_end(source)
                    else:self.retry_after[source]=time.monotonic()+30
                while len(self.cache)>512:self.cache.popitem(last=False)
                if len(self.retry_after)>512:self.retry_after.clear()
                self.status = 'Названия и кнопки'
            except asyncio.CancelledError:raise
            except Exception:
                for source in sources:self.retry_after[source]=time.monotonic()+10
                self.status = 'Кнопки переведены; перевод названий временно недоступен'
            if any(a['role']=='title' and a['source'] not in self.cache and
                   self.retry_after.get(a['source'],0)<time.monotonic() for a in self.anchors):
                self.wakeup.set()

    async def _publish(self):
        previous = None
        while True:
            await asyncio.sleep(.1)
            mirror = self.mirror
            captions = []
            if self.enabled and self.ready and time.monotonic()-self.last_layout_at<4:
                for anchor in self.anchors:
                    target=anchor['translation'] if anchor['role'] in ('button','notice') else self.cache.get(anchor['source'])
                    if not target:continue
                    if (not anchor.get('scroll_box') and 'signature' in anchor and mirror.latest_image is not None and
                        not unchanged(mirror.latest_image,anchor)):
                        continue
                    # iPhone 0.3 already renders title rectangles. Keep that
                    # wire role for fixed status text; never make it clickable.
                    captions.append({'text':target,'box':anchor['box'],'role':'title' if anchor['role']=='notice' else anchor['role'],
                                     'kind':anchor['role'],
                                     'lines':anchor['lines'],'background':anchor.get('background','#ffffff'),
                                     'scroll_ref':anchor.get('scroll_ref',0),
                                     'scroll_box':[v/(mirror.width if i%2==0 else mirror.height) for i,v in enumerate(anchor['scroll_box'])] if anchor.get('scroll_box') else None})
            state = {'captions':captions,'translation_status':self.status,
                     'translation_ready':self.ready,'translation_enabled':self.enabled}
            if state != previous:
                mirror.captions = captions
                await mirror.broadcast({**state,'scroll_offset':getattr(mirror,'scroll_offset',0)})
                previous = state
