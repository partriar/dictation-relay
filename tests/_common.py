"""Shared test setup. Import this FIRST in every test.

- puts tests/stubs (fake sounddevice/pynput/pyperclip/pystray), laptop/ and shared/ on sys.path
- FAKE_WAV: the "microphone" audio. Uses $DICTATION_TEST_WAV if set, else tests/test.wav if present
  (*.wav is gitignored), else generates a short synthetic tone. The fake server returns canned text,
  so the audio content does not matter for these tests.
- start_fake_server(): tiny in-process WebSocket server speaking the relay /ws protocol on a free
  local port, so the relay can be exercised with no phone and no model.
"""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "shared", ROOT / "laptop", ROOT / "tests" / "stubs"):
    sys.path.insert(0, str(p))

CANNED_TEXT = "this is a test sentence"
TOKEN = "testtok"


def _find_wav() -> str:
    env = os.environ.get("DICTATION_TEST_WAV")
    if env:
        return env
    local = ROOT / "tests" / "test.wav"
    if local.exists():
        return str(local)
    import numpy as np
    out = Path(tempfile.gettempdir()) / "dictation_relay_synthetic.wav"
    t = np.arange(16000 * 6) / 16000
    a = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(a.tobytes())
    return str(out)


os.environ["FAKE_WAV"] = _find_wav()   # read by tests/stubs/sounddevice.py at import time


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_fake_server(port: int, host: str = "127.0.0.1") -> None:
    """Fake phone: auth -> ready; on {"type":"done"} replies a canned final."""
    import websockets
    from websockets.asyncio.server import serve

    async def handler(ws):
        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth" or msg.get("token") != TOKEN:
            await ws.close(4001, "unauthorized")
            return
        await ws.send(json.dumps({"type": "ready", "sample_rate": 16000, "format": "s16le"}))
        n = 0
        async for m in ws:
            if isinstance(m, bytes):
                n += len(m)
                continue
            d = json.loads(m)
            if d.get("type") == "done":
                await ws.send(json.dumps({"type": "final", "text": CANNED_TEXT, "audio_s": n / 32000,
                                          "tail_ms": 1, "rtf": 0.1}))
                n = 0

    async def main():
        async with serve(handler, host, port):
            await asyncio.Future()

    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
    import time
    for _ in range(50):
        try:
            socket.create_connection((host, port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("fake server did not start")
