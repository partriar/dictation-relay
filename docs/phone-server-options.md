# Phone server options (`phone/server.py`)

Run in Termux: `python ~/server.py [options]`. The boot script `phone/phone-autostart.sh` runs it for you.

| Flag | Default | What it does |
|---|---|---|
| `--host` | `0.0.0.0` | Address to listen on (all interfaces by default) |
| `--port` | `8765` | Listening port. Must match `port` in the laptop's `config.toml` |
| `--model-dir` | built-in default path (`DEFAULT_MODEL_DIR` in server.py) | Folder with the sherpa-onnx streaming model |
| `--threads` | `4` | CPU threads used for decoding |
| `--tail-pad` | `0.7` | Seconds of silence added when the laptop sends `done` (the laptop's own `tail_pad` overrides it per utterance) |
| `--token` | `$RELAY_TOKEN`, else `~/.dictation_relay_token` | Shared secret the laptop must present |
| `-v`, `--verbose` | off | More logging |

## Keyboard endpoint query parameters (`/client/ws/speech`)

| Parameter | Default | What it does |
|---|---|---|
| `auto_stop` | off | `1` makes the server finish by itself after a pause: one `final:true` message, then it closes the socket (same as `EOS`). Also finishes with an empty final if no text appears within 10 s of audio. Anything else: manual `EOS` only |
| `pause_ms` | `1200` | Pause length (audio time) before auto-stop, clamped to 400-5000. Only used with `auto_stop=1`. Text arrives in ~560 ms chunks, so add up to ~0.6 s |
