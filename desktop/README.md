# Local Pinduoduo translation prototype

This is a local PC prototype. LAN playback and touch are verified; the full goods-detail flow remains unresolved.

## WinClose boundary

The user supplied WinClose as an example of screen streaming. The attempted
reuse of their existing pairing replaced their ordinary PC screen source and
was rolled back on 2026-10-29. The original WinClose executable was restarted;
its configuration and pairing files were not edited.

The translator no longer imports a WinClose sender, discovers its installation,
reads its credentials, or exposes WinClose sender endpoints. The independent
phone_link.py owns separate LAN pairing. Do not restore the former integration. See AGENTS.md.
Any future iPhone connection must have independent pairing and transport.

## Local PC use

Run desktop/Start-Pinduo.ps1, then open http://127.0.0.1:18889/ on the PC.
The launcher starts only the portable Android environment, local translation
model and loopback viewer. No WinClose startup or settings are involved.

The official Pinduoduo Android APK runs in a dedicated emulator on the PC.
The user's physical phone remains an iPhone. The read-only native layout bridge
selects product-name elements and an allowlist of shopping/navigation actions.
Full-screen OCR is disabled. Prices, sales counters, badges and text in photos
are excluded. English titles use a small local Argos/CTranslate2 model; Chinese
titles use the local Tencent model. Buttons use fixed Russian action labels.
Images, recognized text and the bounded phrase cache stay in RAM. No content is
sent to a translation service. Translation is imperfect; 100% accuracy is not
promised. Generated translations must preserve the source's numeric values.

Captions are clipped to native text rectangles, use ellipsis when needed, and
never grow into another card or image. Hover shows the complete translated title.
Video transmission does not wait for the translation snapshot decoder. Its queue
is bounded and browser drawing paints only the latest frame per animation tick.

The default fast profile uses a 480×1040 virtual Android display at density 213,
preserving approximately 360×780 logical dp. This renders 56% fewer pixels than
720×1560. The video cap is 60 fps; actual cadence depends on the Android app.
Pointer moves are coalesced per browser animation frame. During scrolling, a
lightweight native title query replaces full-tree traversal. Sparse optical flow
tracks the card movement between queries, and captions move within their scroll
viewport instead of clearing on touch or wheel input. Prices and photo text remain
untouched. Slightly lower source-image detail is the tradeoff; Russian text stays crisp.

On this PC, the synthetic animated viewer measured 59 fps. The live Pinduoduo
source varied around 20–45 fps during scrolling: stable 45–60 fps is not verified.
The dedicated emulator was increased to six virtual CPUs and 5 GB RAM to reduce
guest swapping. Motion tracking measured roughly 4–7 ms after warm-up.

Layout requests have no 15-second idle disconnect. Unchanged revisions reuse a
cached native tree with a periodic full refresh. Transient failures reconnect;
they do not permanently stop translation. Switching translation OFF/ON on an
unchanged page also restores its captions. Regression tests cover both cases.

The prototype supports pointer input, mouse-wheel scrolling, text entry and adjustable
caption size/color/background. Typing preserves the selected translation toggle. Editable/password text and non-Pinduoduo system applications are excluded by
the native layout provider. Only selected title text reaches a translation engine.

Keep port 18889 on 127.0.0.1. It has Host/Origin checks, not phone authentication.
Do not expose it to a LAN or public tunnel. Model inference is also loopback-only,
with a random local key in ignored .audit/.

## Independent iPhone client (0.4)

The iPhone app is now a single fullscreen remote client. On the desktop viewer choose “iPhone” →
“Показать QR-код”; scan it using the client. The old browser and screen-capture tabs are removed. Both devices must be on the same home
LAN. The PC stays on. The app displays the same Android session and forwards touch,
back and keyboard input; caption size and colors can be adjusted on the phone.

This uses a separate native WSS connection on the PC's RFC1918 address, port
18890. The native app pins the exact server certificate from the QR code and sends
a random pairing secret in the Authorization header. Browser origins and missing
tokens are rejected. The desktop control page and translation model stay on
loopback. No public tunnel, account credential or third-party pairing is involved.
Turn the phone link off from the same dialog when no longer needed. Do not share
the QR code. Its key, certificate and pair file live only in ignored .audit/phone-link/.

The iOS 26/27 0.4 builds pass CI, including fullscreen geometry checks. Version
0.4 (4) was verified installed over USB; the owner confirmed no legacy tabs or
side letterboxing. The goods-detail and search failures remain unresolved. On 2026-10-29 the owner confirmed real-device LAN playback
and scrolling. Stable 45–60 fps is still not verified.

## Verified components

- Official Android Emulator boots using WHPX; official Pinduoduo APK launches.
- The local H.264 viewer and real native test-app button input work.
- Selection tests exclude price/sales badges, image text and generic dialog titles.
- Native layout reads measured 0.05–0.18 s on this PC; four short English titles
  took about 0.4 s in the local CPU translation test. Buttons do not invoke a model.
- A regression test forces GC and socket failure, verifying control retention
  and reconnect instead of leaving a misleading visible but unresponsive frame.
- The independent phone-link test uses real TLS and synthetic data. It checks
  certificate fingerprints, token/Origin rejection, video delivery, reverse touch,
  pointer ownership and disconnect cleanup. It uses no real account content.

Run: python -m unittest discover -s desktop -p 'test_*.py' -q

The owner confirmed on 2026-10-29 that translated titles reappeared and scrolling
became smoother with this fast profile. This verifies the PC prototype only.

On the same date, product taps were verified to navigate to the native goods
activity, but three sampled products returned Pinduoduo's sold-out/recommendations
page. Session renewal did not resolve the sampled failure; the owner reports that
the original iPhone app opens goods. The cause of this discrepancy is unresolved.
The exact sold-out notice is now translated in its original label rectangle,
including on the existing iPhone 0.3 client. The notice is a server/app response,
not independent confirmation that the product is actually unavailable.
The app's own Country/Region & Language settings originally showed Kazakhstan
and English. Testing Chinese Mainland, Chinese UI language and an app restart
also reproduced the sold-out response. These settings were restored afterward;
do not assume that changing the market fixes this problem.

If rebuilding the helper APK is needed, run desktop/Build-Layout.ps1 with the
already prepared Android SDK/JDK. The helper exists only in the dedicated emulator;
it is not an iPhone application or a Windows application installation.

## Private runtime data

SDKs, weights, vendor downloads, emulator user data, signing keys and logs remain
in ignored .validation-deps/ and .audit/. The emulator may contain account state
entered by the owner. Never publish or erase it as a disposable build cache.
This prototype is not packaged for installation on another PC.

## Physical iPhone source candidate — not enabled

The installed pymobiledevice3 11.46.0 includes CoreDevice DisplayService video
streaming and UniversalHIDService touch/keyboard reports. Its implementation
specifically documents the authentication settle interval for iPhone 11. These
are candidates for a physical iPhone host, not a tested backend for this project.
A second trusted device, a local developer-service tunnel, a mounted developer
image and on-device verification are still required. No host tunnel or control
service has been started as part of the 0.4 client update.

The source phone would keep the original Pinduoduo in the foreground. A different
iPhone would run our client; using one phone for both creates a screen feedback
loop. The old capture UI must not be reintroduced into the client.

Before enabling a physical backend, verify original goods navigation, live video
cadence, continuous input, source-app boundaries, native text geometry and cleanup.
Keep all device identifiers and pairing records out of public diagnostics.

The search error “系统繁忙 / 请稍后再试” means the source Pinduoduo reports
“system busy / try again later”. The exact notice and Refresh/Retry actions are
now translated; this does not repair the native app's failed search request.
Network checks found no emulator HTTP proxy, DNS resolution failures, clock skew
or overriding split default VPN route. This still does not establish a particular
server-side rejection reason. Do not describe the cause as proven anti-emulator
blocking without additional evidence.
