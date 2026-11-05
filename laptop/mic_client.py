# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=13", "numpy", "sounddevice"]
# ///
"""
Dictation relay - milestone 2: stream the laptop mic to the phone.

  uv run mic_client.py                 # Enter to start, Enter to stop, q to quit
  uv run mic_client.py --list-devices  # show mics
  uv run mic_client.py --device 3      # pick a mic by number

Prints partials live while you talk, then the final text and the
release->final latency (time from pressing Enter to stop, to final text).

Token comes from --token or the RELAY_TOKEN environment variable.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import time

import numpy as np
import sounddevice as sd
import websockets

from relay_config import client_target

SR = 16000
FRAME_MS = 100


class MicStreamer:
    """Captures mic audio and hands 16 kHz int16 frames to an asyncio queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, device):
        self.loop = loop
        self.device = device
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.stream = None
        self.in_rate = SR

    def _callback(self, indata, frames, time_info, status):
        a = indata[:, 0].astype(np.float32)
        if self.in_rate != SR:  # linear resample to 16 kHz
            n_out = int(round(a.size * SR / self.in_rate))
            a = np.interp(np.linspace(0, a.size - 1, n_out), np.arange(a.size), a).astype(np.float32)
        pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
        self.loop.call_soon_threadsafe(self.queue.put_nowait, pcm)

    def start(self):
        # Ask for 16 kHz directly (Windows usually resamples for us). If the
        # device refuses, capture at its native rate and resample ourselves.
        for rate in (SR, None):
            try:
                self.in_rate = rate or int(sd.query_devices(self.device, "input")["default_samplerate"])
                self.stream = sd.InputStream(
                    samplerate=self.in_rate, channels=1, dtype="float32",
                    blocksize=self.in_rate * FRAME_MS // 1000,
                    device=self.device, callback=self._callback,
                )
                self.stream.start()
                return
            except Exception as e:  # noqa: BLE001
                if rate is None:
                    raise
                last = e
        raise last  # pragma: no cover

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def show_partial(text: str):
    width = shutil.get_terminal_size((100, 20)).columns - 4
    tail = text if len(text) <= width else "…" + text[-(width - 1):]
    sys.stdout.write("\r  " + tail.ljust(width))
    sys.stdout.flush()


async def connect(url: str, token: str):
    ws = await asyncio.wait_for(websockets.connect(url, max_size=2**20), timeout=2)
    await ws.send(json.dumps({"type": "auth", "token": token}))
    try:
        json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
    except websockets.ConnectionClosed as e:
        code = e.rcvd.code if e.rcvd else "?"
        reason = e.rcvd.reason if e.rcvd else ""
        raise SystemExit(f"Phone refused connection: {code} {reason}")
    return ws


async def run(args):
    url = f"ws://{args.host}:{args.port}/ws"
    loop = asyncio.get_running_loop()
    mic = MicStreamer(loop, args.device)

    def reachable_msg(e):
        return f"Can't reach phone at {url} ({type(e).__name__}). Is the server running and on the same Wi-Fi?"

    try:
        t = time.perf_counter()
        ws = await connect(url, args.token)
        print(f"Connected to phone in {(time.perf_counter() - t) * 1000:.0f} ms.")
    except (OSError, asyncio.TimeoutError) as e:
        sys.exit(reachable_msg(e))

    print("Enter = start talking, Enter again = stop. Type q then Enter to quit.\n")
    latencies = []

    while True:
        cmd = await asyncio.to_thread(sys.stdin.readline)
        if cmd == "" or cmd.strip().lower() == "q":
            break

        # Reconnect if the phone dropped us since last time.
        if ws.state.name != "OPEN":
            try:
                ws = await connect(url, args.token)
                print("(reconnected)")
            except (OSError, asyncio.TimeoutError) as e:
                print(reachable_msg(e))
                continue

        final_fut: asyncio.Future = loop.create_future()

        async def reader():
            try:
                async for m in ws:
                    msg = json.loads(m)
                    if msg["type"] == "partial":
                        show_partial(msg["text"])
                    elif msg["type"] == "final":
                        final_fut.set_result(msg)
                        return
            except websockets.ConnectionClosed as e:
                if not final_fut.done():
                    final_fut.set_exception(e)

        async def sender():
            while True:
                await ws.send(await mic.queue.get())

        rtask = asyncio.create_task(reader())
        mic.queue = asyncio.Queue()
        mic.start()
        stask = asyncio.create_task(sender())
        sys.stdout.write("● recording… (Enter to stop)\n")
        sys.stdout.flush()

        await asyncio.to_thread(sys.stdin.readline)
        t_release = time.perf_counter()
        mic.stop()
        # flush any frames captured before stop
        stask.cancel()
        try:
            while not mic.queue.empty():
                await ws.send(mic.queue.get_nowait())
            await ws.send("done")
            final = await asyncio.wait_for(final_fut, timeout=5)
        except (websockets.ConnectionClosed, asyncio.TimeoutError) as e:
            rtask.cancel()
            print(f"\n  ✗ lost the phone mid-dictation ({type(e).__name__}). Press Enter to retry.\n")
            continue
        ms = (time.perf_counter() - t_release) * 1000
        latencies.append(ms)
        sys.stdout.write("\r" + " " * (shutil.get_terminal_size((100, 20)).columns - 1) + "\r")
        print(f"FINAL: {final['text']}")
        print(f"  release->final {ms:.0f} ms | phone tail {final['tail_ms']} ms | "
              f"audio {final['audio_s']}s | RTF {final['rtf']}\n")

    mic.stop()
    await ws.close()
    if latencies:
        lat = sorted(latencies)
        print(f"{len(lat)} dictations. release->final median {lat[len(lat)//2]:.0f} ms, "
              f"worst {lat[-1]:.0f} ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None, help="phone address (default: first host in config.toml)")
    ap.add_argument("--port", type=int, default=None, help="default: config.toml, else 8765")
    ap.add_argument("--token", default=os.environ.get("RELAY_TOKEN"))
    ap.add_argument("--device", type=lambda s: int(s) if s.isdigit() else s, default=None,
                    help="mic number or name (see --list-devices)")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        default_in = sd.default.device[0]
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                mark = "*" if i == default_in else " "
                print(f"{mark} {i:2d}  {d['name']}  ({int(d['default_samplerate'])} Hz)")
        print("\n* = default. Use --device N")
        return
    if not args.token:
        sys.exit("No token: pass --token or set RELAY_TOKEN")
    args.host, args.port = client_target(args.host, args.port)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
