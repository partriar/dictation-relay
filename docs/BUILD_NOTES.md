# Build notes: phone-powered dictation relay


## The problem
- Laptop: 8-year-old HP Spectre x360, i7-8565U, 16 GB, Windows 11. Local dictation with Handy
  (Parakeet TDT 0.6B) worked but lagged: 1.2 s on a short sentence, 3 s on a 13 s paragraph.
- OpenWhispr (Nemotron/Parakeet) on the same laptop was slower than Handy. Smaller models (Parakeet 110M)
  were fast but less accurate. The CPU is the ceiling; no app swap fixes it.
- First answers were "buy a new laptop" and "a relay is more moving parts than the problem deserves".
  The user pushed back: the phone (a Galaxy S24 Ultra) has a far newer chip. That turned out to be right.

## The idea
Run the speech model on the phone (Samsung S24 Ultra, Termux) and stream laptop mic audio to it.
Streaming matters more than raw speed: a streaming model decodes while you talk, so when you stop only
a short tail is left. Batch models (Handy) start the whole job when you stop, so wait time grows with length.

## Feasibility test (10 minutes, before any code)
- sherpa-onnx 2.0.1 installs as a prebuilt wheel in Termux (Python 3.14). No compiling.
- Model: Nemotron Speech Streaming EN 0.6B, int8, 560 ms chunks (k2-fsa release
  `sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25`).
- Result: RTF 0.15 (6.6 s of audio decoded in 0.97 s), model load 3.9 s, 4 threads. Accurate, punctuated.

## Architecture
```
Laptop (Windows)                                 Phone (Termux)
relay.py  --- WebSocket, 16 kHz mono int16 --->  server.py (model resident in RAM)
 hotkey, mic, paste   <--- partial / final JSON
                                                  /client/ws/speech  <--- Kõnele keyboard on the phone
```
- Protocol v1 (`/ws`): first message `{"type":"auth","token":...}`; binary audio frames;
  `{"type":"done","tail_pad":0.7}` ends an utterance; replies `partial` / `final`. Connection stays open.
- Second endpoint (`/client/ws/speech`) speaks the kaldi-gstreamer-server protocol so Android's
  Kõnele voice keyboard can use the same model locally on the phone.

## Milestones and what happened
1. **Phone server + WAV test client.** Exact transcript first time; release->final 309 ms.
2. **Laptop mic streaming.** 20 s dictation came back 386 ms after stopping.
3. **Hotkey + paste.** Tray app, clipboard paste with restore, pre-roll buffer, logs per dictation.
4. **Reliability.** Termux:Boot autostart with crash-restart loop, wake lock, laptop autostart at login,
   auto-reconnect, single-instance guard, newest-connection-wins on the server.
5. **Networking.** Tailscale on both devices; laptop races the Tailscale and LAN addresses.
6. **Text tidy-up.** Spoken numbers to digits, times, phone numbers, final punctuation.
7. **Phone keyboard.** Kõnele + HeliBoard using the same server on the phone.

## Head-to-head results (same sentences, same app, external timing tool)
| Sentence | Handy (Parakeet TDT 0.6B, laptop) | Relay (Nemotron 0.6B, phone) |
|---|---|---|
| Address (5.7 s) | 1317 ms | 448 ms |
| Price (6.0 s) | 1241 ms | 315 ms |
| Call-back (5.3 s) | 1229 ms | 355 ms |
| Medical (10.4 s) | 2164 ms | 330 ms |
| Paragraph (13 s) | 3035 ms | 481 ms |
| **Median** | **1317 ms** | **355 ms (3.7x)** |
- Handy's wait grows with length; the relay stays flat at 0.3-0.5 s.
- Over Tailscale with the phone on 5G: ~400 ms (adds ~130-200 ms).
- Against Handy on the small Parakeet 110M model: Handy was faster on short sentences (362 ms) but less accurate.
- Accuracy: Handy formats better (digits, final punctuation); both miss medical terms. Hence tidy.py.

## Decisions worth knowing (and why)
- **Streaming over batch.** The whole speed win comes from decoding while speaking.
- **int16 audio, not float32.** Half the bandwidth, native mic format.
- **Paste, not typing.** Fastest and most reliable; clipboard is restored afterwards (text only).
- **Tail pad stays at 0.7 s.** Tested 0.2-0.7 s with audio cut right after the last word: anything under
  0.7 s clipped endings ("apprehension" -> "apprehens"). 560 ms chunks need the padding to flush.
- **No bigger model.** No larger English streaming model exists in sherpa-onnx; the 1120 ms-chunk variant is
  a bit more accurate but doubles the tail. Vocabulary errors need a hotword list, not a bigger model.
- **Hotkey: Caps Lock, tap or hold.** Tap = toggle (tap again to stop), hold = push-to-talk.
  Caps Lock is swallowed so it doesn't toggle capitals; Shift+Caps still does.
  Combos are supported (`--keys lctrl+lshift`) and cancel if another key or a mouse click happens
  while held, so Ctrl+Shift+T etc. still work.
- **Mic always open with a 300 ms pre-roll.** No clipped first syllable. Cost: Windows shows "mic in use".
- **Tidy-up on the laptop, not the phone.** Uses text2num on Windows; tidy.py has a pure-Python fallback
  because text2num (Rust/maturin) won't build in Termux.
- **Tailscale + LAN raced in parallel.** First to answer wins; only the winner authenticates, so the phone
  never sees two clients. Works at home, the office, cafes, or with the phone on 5G.

## Problems hit and fixes (the useful part for anyone repeating this)
| Symptom | Cause | Fix |
|---|---|---|
| Pasting commands into Termux "ate" a line | `termux-setup-storage` asked y/n and consumed the next pasted line | Run interactive commands on their own |
| Relay recorded only ~0.4 s, pasted nothing | User tapped the hotkey (toggle habit from Handy); relay was hold-only | Added tap-to-toggle + hold-to-talk |
| Two relay copies fighting, phone said "busy" (4002) | Autostart copy + console copy both running | Windows named mutex single-instance guard |
| Win key combo would open Start menu | Releasing Win alone opens Start | Swallow Win via low-level hook while combo is held (later moved to Caps Lock) |
| Test window measured 7.8 s for the relay | Relay swallows Caps Lock before the test tool's hook sees it | Start the test tool after the relay (its hook then runs first) |
| Phone "offline" after a while | Samsung dropped Wi-Fi and switched to 5G | Turn off Intelligent Wi-Fi "Switch to mobile data"; then Tailscale made it irrelevant |
| Reconnect locked out for ~20 s after a Wi-Fi drop | Server kept the dead connection and rejected the new one as busy | Newest authenticated connection replaces the old one |
| Connection attempts via a system proxy | websockets client honours proxy settings by default | `proxy=None` |
| Kõnele: "400 Bad Request" / "Unable to complete websocket handshake" | (1) raw spaces in Kõnele's URL query; (2) empty `Sec-WebSocket-Protocol:` header | Lenient request-line parsing + lenient handshake that logs what was sent |
| text2num won't install in Termux | Needs Rust/maturin build | Pure-Python number converter fallback in tidy.py |
| FUTO Keyboard mic didn't route to Kõnele | FUTO's external voice routing is undocumented | Switched to HeliBoard (toolbar mic switches to Kõnele) |
| Kõnele keyboard needs an extra tap | Kõnele's keyboard has no auto-start (only its search panel does) | Planned: own voice keyboard app (dictation-keyboard repo) |

## Phone setup recap
- Termux (F-Droid), Termux:Boot, Termux:Widget, Termux:API not required for wake lock.
- `~/server.py`, `~/tidy.py`, model dir in `~`. Boot script `~/.termux/boot/start-dictation-relay`
  (wake lock + restart loop, logs to `~/relay-server.log`). Widget tasks in `~/.shortcuts/tasks/`.
- Battery: Termux and Termux:Boot set to Unrestricted. Tailscale: Always-on VPN, key expiry disabled.
- Keyboard: HeliBoard (typing) + Kõnele (voice), Kõnele server URL `ws://127.0.0.1:8765/client/ws/speech`.

## Laptop setup recap
- Folder with relay.py, tidy.py, token.txt (not committed). `uv` runs everything; deps are declared inline.
- `install-autostart.cmd` writes a hidden VBS launcher into the Startup folder.
- Logs: relay.log (timings only unless `--log-text`).
