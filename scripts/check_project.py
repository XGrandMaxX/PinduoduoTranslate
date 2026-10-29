"""Check the remote-only app's packaging and absence of retired capabilities."""
from pathlib import Path
import plistlib
import sys
ROOT=Path(__file__).resolve().parents[1]
def check():
    expected={'PinduoTranslateApp.swift','RemoteClientView.swift','RemoteGeometry.swift'}
    assert {p.name for p in (ROOT/'App').glob('*.swift')}==expected, 'Unexpected app source'
    for folder in ('Shared','BroadcastExtension','BrowserResources'):
        assert not (ROOT/folder).exists(), f'Retired feature directory: {folder}'
    project=(ROOT/'project.yml').read_text(encoding='utf-8')
    for token in ('ScreenBroadcast','BrowserResources','ReplayKit','ScreenCaptureKit','WebKit','App.entitlements'):
        assert token not in project, f'Retired dependency: {token}'
    for name in ('App-Info.plist','App27-Info.plist'):
        value=plistlib.loads((ROOT/'Config'/name).read_bytes())
        for key in ('UIBackgroundModes','NSScreenCaptureUsageDescription','AppGroupIdentifier','BroadcastExtensionIdentifier'):
            assert key not in value, f'Retired capability: {name}/{key}'
        assert value['NSLocalNetworkUsageDescription'] and value['NSCameraUsageDescription']
        assert 'NSMicrophoneUsageDescription' not in value
        assert value['UIRequiresFullScreen'] is True
        assert value['NSAppTransportSecurity']=={'NSAllowsLocalNetworking':True}
    entry=(ROOT/'App/PinduoTranslateApp.swift').read_text()
    assert 'RemoteClientView()' in entry and 'TabView' not in entry
    view=(ROOT/'App/RemoteClientView.swift').read_text()
    assert '.resizeAspectFill' in view and 'RemoteGeometry.sourcePoint' in view
    assert '.ignoresSafeArea()' in view
    print('PASS: remote-only sources, fullscreen layout, local pairing permissions, no browser/capture capabilities')
    print('Swift compilation and geometry checks run separately on the Mac build runner.')
if __name__=='__main__':
    try:check()
    except (AssertionError,KeyError,ValueError,OSError) as error:
        print(f'Configuration error: {error}',file=sys.stderr);sys.exit(1)
