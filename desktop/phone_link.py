"""Independent LAN-only TLS link. Never uses another application's identity."""
import asyncio
import contextlib
import datetime
import hashlib
import hmac
import io
import ipaddress
import json
import secrets
import socket
import time
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI,WebSocket,WebSocketDisconnect

from controls import dispatch,release

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'.audit/phone-link'


class LocalTLSServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield  # The main desktop server owns process signal handlers.


def lan_address():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
        probe.connect(('1.1.1.1',80))  # Route lookup only; no packet is sent.
        host=probe.getsockname()[0]
    address=ipaddress.ip_address(host)
    if not any(address in ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
        raise RuntimeError('Не найден IPv4-адрес домашней сети')
    return host


class PhoneLink:
    def __init__(self,mirror,translator,private=PRIVATE):
        self.mirror,self.translator=mirror,translator
        self.private=private
        self.server=self.task=None
        self.host=None
        self.pair=None
        self.peers=set()
        self.status='Подключение iPhone выключено'
        self.app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
        self.app.websocket('/stream')(self.stream)

    def prepare(self,host=None):
        self.host=host or lan_address()
        self.private.mkdir(parents=True,exist_ok=True)
        key_path=self.private/'tls-key.pem';cert_path=self.private/'tls-cert.pem';pair_path=self.private/'pair.json'
        if not key_path.exists() or not cert_path.exists():
            key=ec.generate_private_key(ec.SECP256R1())
            name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Pinduo personal LAN link')])
            now=datetime.datetime.now(datetime.timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5))
                  .not_valid_after(now+datetime.timedelta(days=365))
                  .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(self.host))]),critical=False)
                  .sign(key,hashes.SHA256()))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        cert=x509.load_pem_x509_certificate(cert_path.read_bytes())
        fingerprint=cert.fingerprint(hashes.SHA256()).hex()
        token=json.loads(pair_path.read_text())['secret'] if pair_path.exists() else secrets.token_urlsafe(32)
        self.pair={'version':1,'host':self.host,'port':18890,'fingerprint':fingerprint,'secret':token}
        pair_path.write_text(json.dumps(self.pair,separators=(',',':')),encoding='utf-8')
        return key_path,cert_path

    async def start(self):
        if self.task and not self.task.done():return self.state()
        key,cert=self.prepare()
        config=uvicorn.Config(self.app,host=self.host,port=18890,ssl_keyfile=str(key),ssl_certfile=str(cert),
                              ws='wsproto',log_level='warning',access_log=False,timeout_graceful_shutdown=3)
        self.server=LocalTLSServer(config)
        async def run():
            try:await self.server.serve()
            except (OSError,SystemExit):self.status='Порт подключения iPhone недоступен'
        self.task=asyncio.create_task(run())
        for _ in range(50):
            await asyncio.sleep(.05)
            if self.server.started:
                self.status='Отсканируйте QR-код в приложении на iPhone'
                return self.state()
            if self.task.done():break
        raise RuntimeError('Не удалось запустить защищённое подключение в домашней сети')

    async def stop(self):
        for peer in tuple(self.peers):
            with contextlib.suppress(Exception):await peer.close(code=1001)
        if self.server:self.server.should_exit=True
        if self.task:
            with contextlib.suppress(TimeoutError,asyncio.CancelledError):await asyncio.wait_for(self.task,4)
        self.task=self.server=None
        self.status='Подключение iPhone выключено'

    def state(self):
        return {'active':bool(self.server and self.server.started and not self.server.should_exit),
                'connected':len(self.peers),'status':self.status}

    def qr(self):
        if not self.pair:raise RuntimeError('Сначала включите подключение iPhone')
        import qrcode
        code=qrcode.make(json.dumps(self.pair,separators=(',',':')))
        result=io.BytesIO();code.save(result,format='PNG');return result.getvalue()

    async def stream(self,ws:WebSocket):
        # Native client only. A web page from any origin cannot use this endpoint.
        expected='Bearer '+(self.pair or {}).get('secret','')
        offered=ws.headers.get('authorization','')
        if not self.pair or ws.headers.get('origin') or not hmac.compare_digest(offered,expected) or len(self.peers)>=2:
            await ws.close(code=1008);return
        await ws.accept()
        self.peers.add(ws)
        owner=secrets.token_hex(12)
        queue=await self.mirror.subscribe()
        async def send():
            while True:
                message=await queue.get()
                if message is None:
                    await ws.close(code=1013);return
                if isinstance(message,bytes):await ws.send_bytes(message)
                else:await ws.send_json(message)
        sender=asyncio.create_task(send())
        window=time.monotonic();count=0
        try:
            while True:
                raw=await ws.receive_text()
                if len(raw)>10000:break
                now=time.monotonic()
                if now-window>=1:window=now;count=0
                count+=1
                if count>240:break
                await dispatch(json.loads(raw),self.mirror,self.translator,owner)
        except (WebSocketDisconnect,ValueError,OSError):pass
        finally:
            self.peers.discard(ws);self.mirror.clients.discard(queue)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError,WebSocketDisconnect,RuntimeError):await sender
            await release(self.mirror,owner)
