"""Loopback-only development viewer. No Android screen or input is logged."""
import asyncio
import contextlib
import json
import math
import time
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from mirror import Mirror
from translation import Translator
from controls import dispatch,release
from phone_link import PhoneLink

WEB = Path(__file__).parent / 'web'
ROOT=Path(__file__).resolve().parents[1]
mode_path=ROOT/'.audit/source-mode.json'
mode=json.loads(mode_path.read_text()) if mode_path.exists() else {'source':'android'}
if mode.get('source')=='iphone':
    from ios_mirror import IOSMirror
    from ios_layout import IOSLayoutFeed
    mirror=IOSMirror()
    translator=Translator(mirror,feed=IOSLayoutFeed(mirror))
elif mode.get('source')=='android':
    mirror=Mirror()
    translator=Translator(mirror)
else:
    raise RuntimeError('Unknown screen source')
phone=PhoneLink(mirror,translator)
ALLOWED_HOSTS = {'127.0.0.1:18889', 'localhost:18889'}
ALLOWED_ORIGINS = {f'http://{host}' for host in ALLOWED_HOSTS}


@asynccontextmanager
async def lifespan(app):
    await mirror.start()
    await translator.start()
    try:
        yield
    finally:
        await phone.stop()
        await translator.close()
        await mirror.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.headers.get('host') not in ALLOWED_HOSTS:
        return JSONResponse({'error': 'Invalid host'}, status_code=403)
    if request.headers.get('origin') not in (None, *ALLOWED_ORIGINS):
        return JSONResponse({'error': 'Cross-origin access is disabled'}, status_code=403)
    if request.headers.get('sec-fetch-site') == 'cross-site':
        return JSONResponse({'error': 'Cross-site access is disabled'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/')
async def index():
    return FileResponse(WEB / 'index.html')


@app.get('/health')
async def health():
    state = mirror.state()
    # No screen text or account information in diagnostics.
    return {**{k: v for k, v in state.items() if k != 'captions'},
            'translation_ready': translator.ready, 'translation_enabled': translator.enabled,
            'layout_seconds': translator.layout_seconds, 'translation_seconds': translator.translation_seconds,
            'selected_titles':translator.selected_titles,'selected_buttons':translator.selected_buttons,
            'layout_failures':translator.layout_failures,'layout_error':translator.last_layout_error,
            'translation_status':translator.status,'page_notice':translator.page_notice,
            'viewer':mirror.viewer_stats,'phone':phone.state()}


@app.get('/phone')
async def phone_state():return phone.state()


@app.post('/phone/start')
async def phone_start():
    if getattr(mirror,'source_kind',None)=='iphone':
        raise HTTPException(409,'iPhone сейчас является источником экрана. Управление доступно на ПК.')
    try:return await phone.start()
    except RuntimeError as error:raise HTTPException(409,str(error)) from None


@app.post('/phone/stop')
async def phone_stop():
    await phone.stop()
    return phone.state()


@app.get('/phone/qr.png')
async def phone_qr():
    if not phone.pair or not phone.state()['active']:raise HTTPException(409,'Сначала включите подключение')
    return Response(phone.qr(),media_type='image/png')


@app.post('/open-pinduoduo')
async def open_pinduoduo():
    if getattr(mirror,'source_kind',None)=='iphone':
        try:await mirror.open_pinduoduo()
        except Exception:raise HTTPException(409,'Не удалось открыть Pinduoduo на iPhone') from None
    else:
        await mirror.adb('shell', 'am', 'start', '-n',
                         'com.xunmeng.pinduoduo/com.xunmeng.pinduoduo.ui.activity.MainFrameActivity')
    return {'opened': True}


@app.websocket('/stream')
async def stream(ws: WebSocket):
    if ws.headers.get('host') not in ALLOWED_HOSTS or ws.headers.get('origin') not in ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()
    owner=secrets.token_hex(12)
    queue = await mirror.subscribe()
    queue.put_nowait({'translation_status':translator.status,'translation_ready':translator.ready,
                      'translation_enabled':translator.enabled})

    async def send():
        while True:
            message = await queue.get()
            if message is None:
                await ws.close(code=1013)
                return
            if isinstance(message, bytes):
                await ws.send_bytes(message)
            else:
                await ws.send_json(message)

    sender = asyncio.create_task(send())
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 10000:
                break
            message = json.loads(raw)
            if not isinstance(message, dict):break
            await dispatch(message,mirror,translator,owner)
    except (WebSocketDisconnect, ValueError, OSError):
        pass
    finally:
        mirror.clients.discard(queue)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await sender
        await release(mirror,owner)


app.mount('/assets', StaticFiles(directory=WEB), name='assets')
