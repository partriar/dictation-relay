# Dictation relay: build brief

Status: scaffold brief, written on Sonnet 5.5. Build to be done in a fresh Cowork session on Opus 5.

## Goal
Hotkey dictation on a Windows 11 laptop that feels instant, by running the speech model on a Samsung S24 Ultra (Termux) and streaming mic audio to it over Wi-Fi. Text lands at the cursor on the laptop. Fully local: audio never leaves the home/office LAN.

## Why this exists
- Laptop: 8-year-old i7, 16GB RAM, Windows 11. Handy with Parakeet TDT 0.6B is the best local setup tried so far, but still laggy.
- OpenWhispr with Nemotron/Parakeet on the laptop was slower than Handy (tested, abandoned). No app swap fixes the CPU limit.
- Phone test: Nemotron Speech Streaming EN 0.6B int8 (560ms chunks) via sherpa-onnx in Termux.
  - Result: RTF 0.15 (6.6s audio decoded in 0.97s), model load 3.88s, num_threads=4. Transcript accurate with punctuation and capitalisation.
- FUTO Voice Input on the same phone with the Nemotron streaming model felt instant. Streaming (decode while speaking) is the main reason it feels faster than batch models.
- Interim plan: a new MacBook Air will eventually replace this, so keep the build small and disposable.

## Environment (verified)
- Phone: S24 Ultra, Termux from F-Droid, Python 3.14.8, sherpa-onnx 2.0.1 (prebuilt Android wheel, no compile needed).
- Model dir on phone: ~/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25 (encoder.int8.onnx, decoder.int8.onnx, joiner.int8.onnx, tokens.txt).
- Recognizer config that works: OnlineRecognizer.from_transducer(tokens, encoder, decoder, joiner, num_threads=4, sample_rate=16000, feature_dim=80, decoding_method="greedy_search").
- Test script already on phone: ~/test_asr.py (uses accept_waveform, input_finished, is_ready/decode_stream, get_result; result may be str or object with .text).
- Laptop: Windows 11, Handy installed (keep as fallback).
- Phone reachable over LAN (test passed, 5 Nov 2026); address goes in laptop/config.toml.

## Architecture
- Phone: a Python WebSocket server (pip install websockets in Termux). Loads the model once at startup and keeps it resident.
- Laptop: a Python client with a global push-to-talk hotkey, mic capture, WebSocket streaming, and text insertion.
- Protocol (keep it simple):
  - Client connects to ws://PHONE_IP:PORT/ws with a shared-secret token (header or first message).
  - Client sends binary frames: 16 kHz mono PCM, float32 or int16 (pick one, document it), roughly 100-200ms per frame.
  - Server feeds the frames into one OnlineRecognizer stream per connection and sends back JSON: {"type":"partial","text":...} as results change.
  - Client sends text "done" on key release. Server pads about 0.7s of silence, calls input_finished(), drains the decoder, and replies {"type":"final","text":...}.
  - One session at a time is fine.

## Key decisions and reasons
- Streaming WebSocket, not a batch HTTP endpoint: most of the speed comes from decoding while speaking, so on release only a short tail remains. A batch endpoint would give phone-speed Parakeet but not that finish. If streaming proves flaky, a batch OpenAI-compatible /v1/audio/transcriptions fallback would let Handy forks or OpenWhispr's Self-Hosted mode talk to the phone with no custom client.
- Push-to-talk, commit on release: partial results revise themselves, so typing them live causes backspace chaos. Default is to insert the final text on release. Optional later: live-type only stable (unchanged for N chunks) prefix text.
- Text insertion on Windows: clipboard paste (save and restore clipboard, send Ctrl+V) is fastest and most reliable. Fall back to simulated typing for apps that block paste.
- Chunk size: start with the 560ms model (docs say larger chunks are more accurate). Try the 160ms model later if latency matters more than accuracy.
- Language: Python on both ends. Go would normally suit a long-running service, but the prebuilt sherpa-onnx wheel on Termux is Python; flag it if a Go port becomes worthwhile.
- No LLM cleanup, no cloud, no extras. Speed is the only goal.

## Network and security
- Same Wi-Fi LAN only. Give the phone a DHCP reservation or use a stable hostname, otherwise the IP changes.
- Bind the server to the LAN interface, require the shared token, and reject unauthenticated clients. Do not expose to the internet.
- Windows firewall: the laptop only makes outbound connections, so it should not need a rule.
- If used away from home: Tailscale on both devices (user prefers local-first, so keep it optional).

## Phone reliability (Android is hostile to background servers)
- Run termux-wake-lock (needs the Termux:API app) and disable battery optimisation for Termux.
- Keep the phone on a charger while serving; watch for thermal throttling on long sessions (log RTF over time).
- Optional later: Termux:Boot to start the server on boot, Termux:Widget for a start/stop button.
- Add a heartbeat so the client can show "phone offline" quickly instead of hanging.

## Client behaviour
- Hold hotkey to talk, release to insert. Show a small indicator for recording and sending.
- Log latency for every dictation: time from key release to text inserted. This is the metric to optimise.
- If the phone is unreachable within about 1s, show an error. Optional: fall back to Handy.
- Mic device selection and 16 kHz resampling (sounddevice; resample if the device rate differs).

## Milestones
1. Phone server: accept a WAV over WebSocket and return text (test from a laptop script).
2. Streaming from the laptop mic, print partials in the terminal.
3. Hotkey plus paste insertion. Measure release-to-text latency against Handy (same clip, same app).
4. Reliability pass (wake lock, reconnects, heartbeat).
5. Optional: 160ms model comparison, live-typing of stable text, batch /v1/audio/transcriptions fallback.

## Acceptance
- Release-to-text latency clearly lower than Handy with Parakeet TDT 0.6B on short and medium clips.
- Accuracy at least as good on real content (street names, suburbs, later medical terms).
- Survives the phone screen sleeping and a Wi-Fi reconnect.

## Open questions to verify during the build
- Does the websockets package install cleanly in Termux (pure Python, expected yes)?
- Best num_threads on the S24 (try 2, 4, 6) versus heat.
- Does the phone stay fast over a long session, or does it throttle?
- Windows hotkey library choice (pynput vs keyboard) and any antivirus or permission friction.
- Is a 1s tail pad needed, or is the 0.7s from the test enough for clean endings?
