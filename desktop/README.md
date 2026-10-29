# Local Pinduoduo translation prototype

This is a local PC prototype. A usable independent iPhone client is not ready.

## WinClose boundary

The user supplied WinClose as an example of screen streaming. The attempted
reuse of their existing pairing replaced their ordinary PC screen source and
was rolled back on 2026-10-29. The original WinClose executable was restarted;
its configuration and pairing files were not edited.

The translator no longer imports a WinClose sender, discovers its installation,
reads its credentials, or exposes phone/start endpoints. The former sender module
is a documentation-only tombstone. Do not restore this integration. See AGENTS.md.
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
caption size/color/background. Text entry disables translation. Editable/password text and non-Pinduoduo system applications are excluded by
the native layout provider. Only selected title text reaches a translation engine.

Keep port 18889 on 127.0.0.1. It has Host/Origin checks, not phone authentication.
Do not expose it to a LAN or public tunnel. Model inference is also loopback-only,
with a random local key in ignored .audit/.

## Independent iPhone client (0.3)

The iPhone app has a separate “С ПК” tab. On the desktop viewer choose “iPhone” →
“Показать QR-код”; scan it using that tab. Both devices must be on the same home
LAN. The PC stays on. The app displays the same Android session and forwards touch,
back and keyboard input; caption size and colors can be adjusted on the phone.

This uses a separate native WSS connection on the PC's RFC1918 address, port
18890. The native app pins the exact server certificate from the QR code and sends
a random pairing secret in the Authorization header. Browser origins and missing
tokens are rejected. The desktop control page and translation model stay on
loopback. No public tunnel, account credential or third-party pairing is involved.
Turn the phone link off from the same dialog when no longer needed. Do not share
the QR code. Its key, certificate and pair file live only in ignored .audit/phone-link/.

The iOS 26/27 unsigned builds pass CI; 0.3 (3) was verified installed over USB on
the owner's iPhone. On 2026-10-29 the owner confirmed real-device LAN playback
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

If rebuilding the helper APK is needed, run desktop/Build-Layout.ps1 with the
already prepared Android SDK/JDK. The helper exists only in the dedicated emulator;
it is not an iPhone application or a Windows application installation.

## Private runtime data

SDKs, weights, vendor downloads, emulator user data, signing keys and logs remain
in ignored .validation-deps/ and .audit/. The emulator may contain account state
entered by the owner. Never publish or erase it as a disposable build cache.
This prototype is not packaged for installation on another PC.
