"""The only input commands accepted by desktop and the independent iPhone link."""
import math
import time


async def dispatch(message,mirror,translator,owner):
    if not isinstance(message,dict):return
    kind=message.get('type')
    if kind=='touch':
        values=[message.get(k) for k in ('action','pointer','x','y')]
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in values):return
        action,pointer,x,y=values
        if int(action) not in (0,1,2,3) or not 0<=int(pointer)<10:return
        if mirror.active_pointers and mirror.input_owner!=owner:return
        if int(action)==0:mirror.input_owner=owner
        await mirror.touch(int(action),int(pointer),x,y)
        if not mirror.active_pointers:mirror.input_owner=None
    elif kind in ('key','text','scroll'):
        if mirror.active_pointers and mirror.input_owner!=owner:return
        if kind=='key' and isinstance(message.get('code'),int):await mirror.key(message['code'])
        elif kind=='text' and isinstance(message.get('text'),str):
            await translator.set_enabled(False)
            await mirror.text(message['text'][:1000])
        elif kind=='scroll':
            values=[message.get(k) for k in ('x','y','amount')]
            if all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
                await mirror.scroll(values[0],values[1],min(4,max(-4,values[2])))
    elif kind=='translation' and isinstance(message.get('enabled'),bool):
        await translator.set_enabled(message['enabled'])
    elif kind=='keyframe':await mirror._send_control(bytes([17]))
    elif kind=='stats':
        fps=message.get('fps');queue=message.get('queue')
        if isinstance(fps,(int,float)) and math.isfinite(fps) and 0<=fps<=240 and isinstance(queue,int):
            mirror.viewer_stats={'fps':fps,'decode_queue':queue,'sample_at':round(time.monotonic(),1)}


async def release(mirror,owner):
    if mirror.input_owner==owner:
        await mirror.release_pointers()
        mirror.input_owner=None
