# Dictation relay

Fast, private, local voice dictation for an old Windows laptop, powered by the newer Android phone in your pocket.

The phone runs a streaming speech-recognition model. The laptop streams your microphone to it over Wi-Fi (or Tailscale), and the text is pasted at your cursor a fraction of a second after you stop talking. No cloud, no subscription, no audio leaves your own devices.

The same phone server also powers a voice keyboard on the phone itself.

## How it works

```
Laptop (Windows)                                  Phone (Termux)
relay.py  --- WebSocket, 16 kHz mono int16 --->   server.py  (model stays loaded in RAM)
 hotkey, mic, paste   <--- partial / final text   sherpa-onnx + Nemotron streaming model
                                                  /client/ws/speech  <--- Kõnele voice keyboard (on the phone)
```

Why it is fast: a streaming model decodes while you are still talking, so when you stop only a short tail is left. Batch dictation apps start the whole job when you stop, so the wait grows with the length of what you said. A phone chip from the last couple of years is much faster than an 8-year-old laptop CPU, so offloading the model to the phone wins even with the network hop.

## Results

Tested on an 8-year-old i7-8565U laptop and a Galaxy S24 Ultra. Time from releasing the hotkey to text appearing, same sentences, same app, external timing tool. The comparison is [Handy](https://github.com/cjpais/Handy) running Parakeet TDT 0.6B on the laptop.

| Spoken length | Handy on laptop | Relay (phone) |
|---|---|---|
| 5.3 s | 1229 ms | 355 ms |
| 5.7 s | 1317 ms | 448 ms |
| 6.0 s | 1241 ms | 315 ms |
| 10.4 s | 2164 ms | 330 ms |
| 13 s | 3035 ms | 481 ms |
| **Median** | **1317 ms** | **355 ms (3.7x faster)** |

- The relay stays flat at 0.3 to 0.5 s however long you talk.
- Over Tailscale with the phone on 5G: about 400 ms.
- Accuracy is good and punctuated, but like any model it misses unusual vocabulary (medical terms, names). A built-in tidy-up step converts spoken numbers to digits and adds final punctuation.

## Requirements

- Windows 10/11 laptop with a microphone.
- Android phone with roughly 2 GB of free RAM and about 1 GB of storage for the model. Tested on a Galaxy S24 Ultra.
- Phone and laptop on the same Wi-Fi, or both on Tailscale (see below).
- On the phone, from [F-Droid](https://f-droid.org): Termux and Termux:Boot. Do not use the Play Store builds of Termux.
- On the laptop: [uv](https://docs.astral.sh/uv/) (it installs Python and all libraries for you).

## Phone setup

1. Install **Termux** and **Termux:Boot** from F-Droid. Open Termux:Boot once so Android registers it.
2. Open Termux and update it:
   ```
   pkg update
   ```
3. Install Python and the libraries. <!-- VERIFY on next fresh phone setup: python-numpy may be unnecessary -->
   ```
   pkg install python python-numpy
   ```
   ```
   pip install sherpa-onnx websockets
   ```
4. Download and unpack the model (Nemotron Speech Streaming EN 0.6B, int8, 560 ms chunks) into your home folder.
   ```
   cd ~
   ```
   ```
   curl -SL -O https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2
   ```
   ```
   tar xf sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2
   ```
   The server looks for the folder `~/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25` by default. If you use another folder, pass `--model-dir`.
5. Give Termux access to shared storage. **Run this on its own line and answer the prompt before pasting anything else**, otherwise it swallows the next pasted line:
   ```
   termux-setup-storage
   ```
6. Copy `phone/server.py` **and** `shared/tidy.py` from this repo onto the phone (for example into its Downloads folder), then:
   ```
   cp ~/storage/downloads/server.py ~/
   ```
   ```
   cp ~/storage/downloads/tidy.py ~/
   ```
   `tidy.py` must sit next to `server.py`. It has a pure-Python number converter, so nothing extra to install.
7. Start it once by hand to check it works:
   ```
   termux-wake-lock
   ```
   ```
   python ~/server.py
   ```
   It prints the model load time (a few seconds), `Listening on ws://0.0.0.0:8765/ws`, and a **Token** line. The token is generated once and saved in `~/.dictation_relay_token`, so it stays the same across restarts. Copy it for the laptop step. To show it again later: `cat ~/.dictation_relay_token`.
8. Press Ctrl+C to stop, then set up autostart (next section).

### Phone autostart (Termux:Boot)

Copy `phone/phone-autostart.sh` to the phone the same way, then run it once:

```
cp ~/storage/downloads/phone-autostart.sh ~/
```
```
sh ~/phone-autostart.sh
```

It creates `~/.termux/boot/start-dictation-relay`, which takes a wake lock, starts the server at every boot, restarts it 3 seconds after any crash, and logs to `~/relay-server.log`. It also creates optional start/stop buttons for the Termux:Widget app. To start it now without rebooting:

```
nohup ~/.termux/boot/start-dictation-relay >/dev/null 2>&1 &
```

### Phone battery settings

Android will kill background apps unless told not to. In Settings, Apps, set **Termux** and **Termux:Boot** to battery **Unrestricted**. On Samsung phones also turn off Intelligent Wi-Fi's "Switch to mobile data", which silently drops the phone off Wi-Fi.

### Server options

`python ~/server.py [options]`

| Flag | Default | What it does |
|---|---|---|
| `--host` | `0.0.0.0` | Address to listen on |
| `--port` | `8765` | Port. Must match `port` on the laptop |
| `--model-dir` | `~/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25` | Model folder |
| `--threads` | `4` | CPU threads for decoding |
| `--tail-pad` | `0.7` | Seconds of silence added when you stop (the laptop's setting overrides it) |
| `--token` | `$RELAY_TOKEN`, else `~/.dictation_relay_token` | Shared secret |
| `-v` | off | More logging |

## Laptop setup

1. Install uv (PowerShell):
   ```
   winget install --id=astral-sh.uv -e
   ```
2. Get this repo (download the ZIP or `git clone`) and open the `laptop` folder.
3. Create your settings file. Copy `config.example.toml` to `config.toml` in the same folder, then edit it. The only required line is `hosts`: your phone's address(es). Everything else is optional.
   ```toml
   hosts = ["100.x.y.z", "192.168.1.50"]   # Tailscale address, home Wi-Fi address
   port = 8765
   keys = "caps"
   ```
   Find the phone's Wi-Fi address by running `ifconfig` in Termux and looking at `wlan0`. All hosts are tried at once and the first to answer wins. Settings precedence: command-line flag, then `config.toml`, then default. See `config.example.toml` for every option (`device`, `insert`, `tail_pad`, `post_roll`, `trailing_space`, `tidy`, `log_text`).
4. Create `token.txt` in the `laptop` folder containing just the token from the phone, on one line. (It is gitignored. Alternatives: `--token` or the `RELAY_TOKEN` environment variable.)
5. Run it. In PowerShell, from the repo:
   ```
   cd laptop
   ```
   ```
   uv run relay.py
   ```
   uv installs the dependencies on first run. A mic icon appears in the tray (it may be under the ^ overflow arrow). Tray colours: grey ready, red recording, amber transcribing, black phone offline. A low beep means the phone was unreachable or sent no text back.
6. To start it automatically at every login (hidden, tray icon only):
   ```
   uv run relay.py --install-startup
   ```
   Or double-click `install-autostart.cmd`. Undo with `uv run relay.py --uninstall-startup`. Only one copy can run at a time.

### Using it

- **Caps Lock** is the default hotkey.
  - **Tap** it to start, tap again to stop and paste (toggle mode; auto-stops after 120 s).
  - **Hold** it, talk, release (push-to-talk).
  - **Shift+Caps Lock** still toggles real Caps Lock. Plain Caps Lock is swallowed so it does not shift your typing.
- Text is pasted at the cursor with a trailing space. Your clipboard text is restored afterwards (only text; an image on the clipboard is replaced).
- **Combos** work too, e.g. `keys = "lctrl+lshift"` or `--keys lctrl+lshift`. While a combo is held, any other key or a mouse click cancels the recording, so shortcuts like Ctrl+Shift+T still work. Key names: `caps`, `ctrl`, `lctrl`, `rctrl`, `win`, `lwin`, `rwin`, `lalt`, `ralt`, `lshift`, `rshift`, `f13`, `f14`, `f15`.
- The microphone stays open with a 300 ms pre-roll so your first syllable is never clipped. The cost: Windows shows "microphone in use" while the relay runs.

Handy flags (`uv run relay.py --help` for all): `--insert type` for apps that block paste, `--post-roll 0.15` if you release the key too early, `--device N` to pick a mic (list them with `uv run mic_client.py --list-devices`), `--log-text` to also log transcripts (off by default; only timings are logged), `--no-tidy` for raw model text, `--show-keys` to check what your keys send. Run `uv run relay.py` in a console to see logs.

## Tailscale (optional)

[Tailscale](https://tailscale.com) gives your devices private addresses (`100.x.y.z`) that work from anywhere. Install it on both phone and laptop, sign in to the same account, and put the phone's Tailscale address in `hosts` alongside its Wi-Fi address.

Why bother: Wi-Fi addresses change, and phones hop between Wi-Fi and 5G. The relay tries every address in parallel and uses whichever answers first, so it works at home, at the office, in cafes, or with the phone on mobile data. Only the winning connection authenticates, so the phone never sees two clients.

Tips: turn on Always-on VPN for Tailscale on the phone, and disable key expiry for both machines in the Tailscale admin console so they do not drop off after a few months.

## Phone keyboard (Kõnele + HeliBoard)

The same server also speaks the kaldi-gstreamer-server protocol, so you can dictate on the phone itself using the same local model.

1. Install **HeliBoard** (typing keyboard) and **Kõnele** (voice input) from F-Droid.
2. Enable both as keyboards/voice services in Android settings and choose HeliBoard as your keyboard.
3. In Kõnele's settings, set the server URL to:
   ```
   ws://127.0.0.1:8765/client/ws/speech
   ```
4. In HeliBoard, the toolbar microphone switches to Kõnele's voice input.

No token is needed from the phone itself (127.0.0.1). From anywhere else, append `&token=YOUR_TOKEN` to the URL. The laptop and the phone keyboard can be used at the same time. Text is tidied the same way if `tidy.py` is next to `server.py`.

Known limit: Kõnele's keyboard needs one extra tap to start listening. FUTO Keyboard's mic did not route to Kõnele in testing, which is why HeliBoard is used.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pasting commands into Termux "eats" a line | `termux-setup-storage` asks y/n and consumes the next pasted line | Run interactive commands on their own line |
| Tapping the hotkey records only ~0.4 s and pastes nothing | Older hold-only behaviour | Update: tap now toggles, hold is push-to-talk |
| Relay keeps dropping and reconnecting; log shows close code 4002 "replaced by newer connection" | Another client (a second relay on another PC, or a test client) connected to the phone, and the server keeps only the newest | Close the other client. On one PC the single-instance guard already prevents two relays |
| Close code 4001 | Wrong token | Re-copy the token from the phone into `token.txt` |
| Phone shows offline after a while | Phone dropped Wi-Fi for mobile data | Turn off "Switch to mobile data" (Samsung Intelligent Wi-Fi) and use Tailscale |
| Reconnect stalls after a Wi-Fi drop | Server kept the dead connection | The newest authenticated connection now replaces the old one |
| Cannot connect through a corporate or system proxy | WebSocket clients honour proxy settings by default | The relay disables proxy use; check `hosts` and the port |
| Win-key combo opens the Start menu | Releasing Win alone opens Start | Use Caps Lock or a Ctrl/Shift combo instead |
| Kõnele: "400 Bad Request" or handshake failure | Kõnele sends spaces in its URL and an empty protocol header | The server is lenient about both; make sure you copied the current `server.py` |
| Server fails on `import websockets` | Library missing | `pip install websockets` in Termux |
| Word endings clipped ("apprehension" becomes "apprehens") | Tail pad too small | Keep `tail_pad` at 0.7 (the 560 ms model needs the padding to flush) |
| Spoken numbers stay as words | `tidy.py` missing next to `server.py` (phone keyboard) | Copy `shared/tidy.py` to `~` on the phone |
| Server stops after a while | Android killed Termux | Set Termux and Termux:Boot to Unrestricted battery; use the autostart script (it takes a wake lock and restarts on crash) |
| Wrong words for names or jargon | Model vocabulary | A bigger streaming model does not exist for English in sherpa-onnx; a hotword list is the fix |

If the phone cannot be reached: same Wi-Fi? Did its address change (`ifconfig` in Termux, `wlan0`)? Is the server running (`tail ~/relay-server.log`)?

## Protocol (for client writers)

WebSocket endpoint: `ws://PHONE_ADDRESS:8765/ws`

- First message must be text JSON `{"type":"auth","token":"..."}` within 3 s. Reply: `{"type":"ready",...}`. Wrong or missing token closes with code 4001. A second simultaneous client replaces the first (the old connection is dropped).
- Audio: binary frames, **16 kHz mono int16 little-endian**.
- `{"type":"done","tail_pad":0.7}` ends an utterance and returns `{"type":"final","text":...,"tail_ms":...,"rtf":...}`. The connection stays open for the next one.
- `{"type":"partial","text":...}` is sent whenever the hypothesis changes.
- `{"type":"ping"}` returns `{"type":"pong"}`. `{"type":"reset"}` discards the current utterance.
- Second endpoint `/client/ws/speech` speaks the kaldi-gstreamer-server protocol (used by Kõnele). Local clients need no token; others pass `?token=...`.
- Optional auto-stop on that endpoint: add `&auto_stop=1` (and `&pause_ms=1200`, clamped 400-5000). The server then finishes exactly as if `EOS` arrived (one `final`, then close) once the text has not changed for `pause_ms` of audio, or with an empty final if nothing was heard in 10 s of audio. Without `auto_stop=1` nothing changes: only `EOS` or closing ends the utterance.
- The model returns text in about 560 ms chunks, so the real pause before auto-stop is `pause_ms` plus up to roughly 0.6 s.

`laptop/wav_client.py` is a small reference client that sends a WAV file and prints partials, final text and timings.

## Running the tests

The tests use stub versions of the keyboard, mouse, mic and tray libraries, so they run without hardware. From the repo root:

```
uv run --with websockets --with numpy --with pillow --with text2num tests/run_all.py
```

`tests/test_tail_pad.py` needs the real model and runs only if the environment variable `DICTATION_MODEL_DIR` points at the model folder.

## Credits and licences

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) by the k2-fsa project, Apache-2.0: the speech runtime that makes this run on a phone.
- NVIDIA Nemotron Speech Streaming EN 0.6B, exported for sherpa-onnx by k2-fsa and downloaded from the [sherpa-onnx ASR model releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models). The model is released under the [NVIDIA Open Model License](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b); read it before redistributing or using the model commercially.
- [Kõnele](https://github.com/Kaljurand/K6nele) by Kaljurand: the Android voice keyboard that talks to the phone server.
- [HeliBoard](https://github.com/Helium314/HeliBoard): the open-source keyboard used for typing.
- [Tailscale](https://tailscale.com): private networking between your devices.
- [Handy](https://github.com/cjpais/Handy): the local dictation app used as the speed baseline.
- [text2num](https://pypi.org/project/text2num/) for number conversion on the laptop.

## Licence

MIT. See [LICENSE](LICENSE). Models and third-party apps keep their own licences.
