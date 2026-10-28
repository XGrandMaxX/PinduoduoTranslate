"""Validate packaging invariants without Xcode or third-party dependencies."""
from pathlib import Path
import plistlib
import sys

ROOT = Path(__file__).resolve().parents[1]


def check():
    plists = {}
    for path in sorted((ROOT / 'Config').glob('*')):
        if path.suffix in ('.plist', '.entitlements'):
            with path.open('rb') as stream:
                plists[path.name] = plistlib.load(stream)

    app = plists['App-Info.plist']
    extension = plists['Broadcast-Info.plist']
    assert app['AppGroupIdentifier'] == extension['AppGroupIdentifier'] == '$(APP_GROUP_ID)'
    for name in ('App.entitlements', 'Broadcast.entitlements'):
        assert plists[name]['com.apple.security.application-groups'] == ['$(APP_GROUP_ID)']
    assert extension['NSExtension']['NSExtensionPointIdentifier'] == 'com.apple.broadcast-services-upload'
    assert extension['NSExtension']['NSExtensionPrincipalClass'] == '$(PRODUCT_MODULE_NAME).SampleHandler'
    assert app['BroadcastExtensionIdentifier'] == '$(BUNDLE_PREFIX).broadcast'
    assert 'screen-capture' in app['UIBackgroundModes']
    assert app['NSScreenCaptureUsageDescription']
    assert 'NSMicrophoneUsageDescription' not in app
    for folder in ('App', 'BroadcastExtension', 'Shared'):
        assert list((ROOT / folder).glob('*.swift')), f'No Swift sources in {folder}'
    for path in ROOT.rglob('*.mobileprovision'):
        raise AssertionError(f'Provisioning profile must stay outside project: {path.name}')
    print(f'OK: {len(plists)} plist/entitlement files, shared identifiers, capture configuration and source targets')
    print('This does not compile Swift or verify iPhone runtime behavior.')


if __name__ == '__main__':
    try:
        check()
    except (AssertionError, KeyError, ValueError, OSError) as error:
        print(f'Configuration error: {error}', file=sys.stderr)
        sys.exit(1)
