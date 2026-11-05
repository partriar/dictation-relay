# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=13", "numpy"]
# ///
"""
Dictation relay - milestone 1 test client (runs on the laptop).

Sends a WAV file to the phone server in 100 ms frames, prints partials as
they arrive, then the final text and timings.

  uv run wav_client.py --host 100.x.y.z --token XXXX test.wav
  uv run wav_client.py --host 100.x.y.z --token XXXX --realtime test.wav

--realtime paces frames at real speed, which simulates speaking into a mic:
the number that matters is "release->final" (time from 'done' to the final
text arriving). Without it, the whole file is sent as fast as possible.

Token can also come from the RELAY_TOKEN environment variable.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import wave

import numpy as np
import websockets

from relay_config import client_target

SR = 16000
FRAME_MS = 100


def load_wav_16k_mono_s16(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        sys.exit(f"Unsupported sample width {sw}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != SR:  # simple linear resample; fine for speech tests
        t_old = np.arange(a.size) / sr
        t_new = np.arange(int(a.size * SR / sr)) / SR
        a = np.interp(t_new, t_old, a).astype(np.float32)
        print(f"(resampled {sr} Hz -> {SR} Hz)")
    return (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()


async def run(args):
    pcm = load_wav_16k_mono_s16(args.wav)
    audio_s = len(pcm) / 2 / SR
    frame_bytes = SR * FRAME_MS // 1000 * 2
    url = f"ws://{args.host}:{args.port}/ws"

    t_conn = time.perf_counter()
    try:
        ws = await asyncio.wait_for(websockets.connect(url, max_size=2**20), timeout=2)
    except (OSError, asyncio.TimeoutError) as e:
        sys.exit(f"Can't reach phone at {url}: {e}")
    async with ws:
        await ws.send(json.dumps({"type": "auth", "token": args.token}))
        try:
            ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        except websockets.ConnectionClosed as e:
            sys.exit(f"Server closed connection: {e.rcvd.code if e.rcvd else ''} {e.rcvd.reason if e.rcvd else ''}")
        connect_ms = (time.perf_counter() - t_conn) * 1000
        print(f"connected in {connect_ms:.0f} ms -> {ready}")

        # heartbeat round trip
        t = time.perf_counter()
        await ws.send("ping")
        json.loads(await ws.recv())
        print(f"ping rtt {(time.perf_counter() - t) * 1000:.0f} ms")
        print(f"sending {audio_s:.2f}s of audio {'(real-time)' if args.realtime else '(fast)'}")

        partials = 0
        final = {}
        t_done = None

        async def reader():
            nonlocal partials, final
            async for m in ws:
                msg = json.loads(m)
                if msg["type"] == "partial":
                    partials += 1
                    print(f"  partial: {msg['text']}")
                elif msg["type"] == "final":
                    final = msg
                    return

        rtask = asyncio.create_task(reader())
        t_start = time.perf_counter()
        for i in range(0, len(pcm), frame_bytes):
            await ws.send(pcm[i:i + frame_bytes])
            if args.realtime:
                target = t_start + (i + frame_bytes) / 2 / SR
                delay = target - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
        t_done = time.perf_counter()
        await ws.send("done")
        await rtask
        t_final = time.perf_counter()

    print()
    print(f"FINAL: {final.get('text')}")
    note = "<- the latency that matters" if args.realtime else "(includes backlog; use --realtime for a true figure)"
    print(f"  release->final : {(t_final - t_done) * 1000:.0f} ms   {note}")
    print(f"  server tail    : {final.get('tail_ms')} ms  (decode after 'done', excl. network)")
    print(f"  server RTF     : {final.get('rtf')}")
    print(f"  total          : {(t_final - t_start):.2f}s for {audio_s:.2f}s audio, {partials} partials")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--host", default=None, help="phone address (default: first host in config.toml)")
    ap.add_argument("--port", type=int, default=None, help="default: config.toml, else 8765")
    ap.add_argument("--token", default=os.environ.get("RELAY_TOKEN"))
    ap.add_argument("--realtime", action="store_true", help="pace audio at real speed")
    args = ap.parse_args()
    args.host, args.port = client_target(args.host, args.port)
    if not args.token:
        sys.exit("No token: pass --token or set RELAY_TOKEN (server prints it at startup)")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
