"""Prepare exactly one trusted USB iPhone; never print device/route records."""
import asyncio
import contextlib
import json
import logging
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


async def prepare():
    from pymobiledevice3.tunneld.api import get_tunneld_devices
    from pymobiledevice3.services.mobile_image_mounter import auto_mount
    devices=await get_tunneld_devices(('127.0.0.1',18897))
    try:
        if len(devices)!=1:
            raise RuntimeError('Connect exactly one trusted, unlocked iPhone')
        device=devices[0]
        if 'com.apple.coredevice.displayservice' not in device.peer_info.get('Services',{}):
            print('Preparing Apple developer support...',flush=True)
            with (ROOT/'.audit/ios-ddi-mount.log').open('w',encoding='utf-8') as log, contextlib.redirect_stderr(log):
                await auto_mount(device)
        route={'local':device.service.local_address[0],'remote':device.service.address[0],'port':18899}
        (ROOT/'.audit/iphone-media-route.json').write_text(json.dumps(route))
        print('iPhone USB route prepared.',flush=True)
    finally:
        for device in devices:
            await device.close()


if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    try:asyncio.run(prepare())
    except Exception as error:
        print('iPhone preparation failed: '+type(error).__name__,flush=True)
        raise SystemExit(1)
