"""Read-only semantic layout in the dedicated emulator. No OCR, no screen files."""
import asyncio
import contextlib
import json
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APK=ROOT/'.validation-deps/layout-build/layout.apk'


class LayoutChanging(Exception):
    """The native tree changed during traversal; the connection is healthy."""


class LayoutFeed:
    def __init__(self,mirror):
        self.mirror=mirror
        self.reader=self.writer=self.process=None
        self.port=None
        self.log=None
        self.ready=False
        self.seconds=0.0
        self.last_data=None
        self.last_full_at=0.0

    async def start(self):
        self.last_data=None
        self.last_full_at=0.0
        installed=await self.mirror.adb('shell','pm','path','local.pinduo.layout')
        if b'package:' not in installed:
            if not APK.is_file():raise RuntimeError('Build the local layout bridge first')
            await self.mirror.adb('install','--no-streaming',str(APK))
        self.port=int((await self.mirror.adb('forward','tcp:0','tcp:18894')).strip())
        try:
            await self.snapshot()
            return
        except (OSError,ValueError,TimeoutError,asyncio.IncompleteReadError):
            await self._disconnect()
        self.log=(ROOT/'.audit/layout-bridge.log').open('ab')
        from mirror import ADB,SERIAL
        self.process=await asyncio.create_subprocess_exec(str(ADB),'-s',SERIAL,'shell','am','instrument','-w',
            'local.pinduo.layout/.LayoutInstrumentation',stdout=self.log,stderr=self.log)
        for _ in range(30):
            await asyncio.sleep(.2)
            try:
                await self.snapshot()
                return
            except (OSError,ValueError,TimeoutError,asyncio.IncompleteReadError):
                await self._disconnect()
        raise RuntimeError('Semantic layout feed did not start')

    async def snapshot(self,fast=False):
        if not self.writer:
            self.reader,self.writer=await asyncio.open_connection('127.0.0.1',self.port,limit=1024*1024)
        started=time.monotonic()
        known=self.last_data.get('revision',-1) if self.last_data and time.monotonic()-self.last_full_at<2 else -1
        self.writer.write(f'{"fast" if fast else "layout"} {known}\n'.encode())
        await self.writer.drain()
        raw=await asyncio.wait_for(self.reader.readline(),2)
        if not raw:raise OSError('Layout feed closed')
        data=json.loads(raw)
        if isinstance(data,dict) and data.get('fast'):
            self.ready=True
            self.seconds=round(time.monotonic()-started,4)
            return data
        if isinstance(data,dict) and data.get('unchanged') and self.last_data:
            self.ready=True
            self.seconds=round(time.monotonic()-started,4)
            return self.last_data
        if not isinstance(data,dict) or not isinstance(data.get('nodes'),list):raise ValueError('Invalid layout')
        if data.get('retry'):raise LayoutChanging('Layout is changing')
        self.last_data=data
        self.last_full_at=time.monotonic()
        self.ready=True
        self.seconds=round(time.monotonic()-started,4)
        return data

    async def _disconnect(self):
        if self.writer:
            self.writer.close()
            with contextlib.suppress(OSError,TimeoutError):await asyncio.wait_for(self.writer.wait_closed(),1)
        self.reader=self.writer=None

    async def recover(self,restart=False):
        if restart:self.ready=False
        await self._disconnect()
        self.last_data=None
        if restart:
            await self.close()
            await self.start()

    async def close(self):
        self.ready=False
        await self._disconnect()
        if self.port:
            with contextlib.suppress(Exception):
                await self.mirror.adb('shell','am','force-stop','local.pinduo.layout')
        if self.process:
            if self.process.returncode is None:
                self.process.terminate()
                with contextlib.suppress(TimeoutError):await asyncio.wait_for(self.process.wait(),3)
        if self.port:
            with contextlib.suppress(Exception):await self.mirror.adb('forward','--remove',f'tcp:{self.port}')
        if self.log:self.log.close()
        self.process=self.port=self.log=None
