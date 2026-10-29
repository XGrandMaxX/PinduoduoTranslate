"""Regression: GC must not disconnect control while video remains visible."""
import asyncio
import gc
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mirror as transport


class Child:
    returncode=None
    def terminate(self):self.returncode=0
    async def wait(self):return 0


class MirrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_survives_gc_and_reconnects(self):
        connections=0
        resets=asyncio.Queue()
        writers=[]
        async def client(reader,writer):
            nonlocal connections
            number=connections
            connections+=1
            writers.append(writer)
            try:
                if number%2==0:
                    writer.write(b'\0h264'+struct.pack('>III',0x80000000,720,1560))
                    await writer.drain()
                else:
                    message=await reader.readexactly(1)
                    await resets.put(message)
                await reader.read()
            except (OSError,asyncio.IncompleteReadError):pass
            finally:writer.close()
        server=await asyncio.start_server(client,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        mirror=transport.Mirror()
        async def adb(*args):
            if args==('get-state',):return b'device'
            if args==('emu','avd','name'):return b'PinduoLocal\nOK'
            return str(port).encode()
        mirror.adb=adb
        async def spawn(*args,**kwargs):return Child()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'.audit').mkdir()
            with patch.object(transport,'ROOT',root),patch.object(asyncio,'create_subprocess_exec',spawn):
                try:
                    await mirror.start()
                    gc.collect()
                    await asyncio.sleep(.05)
                    self.assertTrue(mirror.control_ready)
                    await mirror._send_control(bytes([17]))
                    self.assertEqual(await asyncio.wait_for(resets.get(),2),b'\x11')
                    mirror.control_writer.close()
                    deadline=asyncio.get_running_loop().time()+5
                    while mirror.session_count<2 or not mirror.control_ready:
                        if asyncio.get_running_loop().time()>deadline:self.fail('Control did not recover')
                        await asyncio.sleep(.05)
                    await mirror._send_control(bytes([17]))
                    self.assertEqual(await asyncio.wait_for(resets.get(),2),b'\x11')
                finally:await mirror.close()
        server.close()
        await server.wait_closed()
        for writer in writers:
            writer.close()
            await writer.wait_closed()


if __name__=='__main__':unittest.main()
