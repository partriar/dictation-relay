#!/usr/bin/env python3
"""
Dictation relay - phone server (runs in Termux on the S24 Ultra).

Loads the Nemotron streaming model once, then accepts one WebSocket client
at a time and streams partial/final transcripts back.

Protocol (v1)
-------------
Connect:   ws://PHONE_IP:8765/ws
Auth:      first message must be text JSON {"type":"auth","token":"..."}
           -> server replies {"type":"ready","sample_rate":16000,"format":"s16le"}
           Wrong/missing token or no auth within 3s -> close 4001.
Audio:     binary frames, 16 kHz mono signed 16-bit little-endian PCM.
           Any frame size works; 100-200 ms (3200-6400 bytes) is the target.
Control:   text "done" or {"type":"done"}  -> end of utterance
           {"type":"done","tail_pad":0.4}   -> same, with custom silence pad (0.1-1.5 s)
           text "ping" or {"type":"ping"}  -> {"type":"pong"}
           {"type":"reset"}                -> discard current utterance
Replies:   {"type":"partial","text":"..."}      whenever the hypothesis changes
           {"type":"final","text":"...","audio_s":..,"tail_ms":..,"rtf":..}
The connection stays open after "final"; send more audio for the next
utterance. Keeping one connection open avoids reconnect cost per dictation.

Second endpoint, for Android voice keyboards (Kõnele) on the phone itself:
           ws://127.0.0.1:8765/client/ws/speech?content-type=...   (kaldi-gstreamer-server protocol)
           binary audio in, text "EOS" to finish; JSON
           {"status":0,"segment":0,"result":{"hypotheses":[{"transcript":..}],"final":bool}} out.
           No token needed from the phone itself (127.0.0.1); from anywhere else add &token=...
           ws://.../client/ws/status reports one available worker (Kõnele checks this).
           Opt-in auto-stop (default off): add &auto_stop=1[&pause_ms=1200] (pause_ms clamped 400-5000).
           The server then finishes as if EOS was sent (one final, then close) once the text has not
           changed for pause_ms of audio, or with an empty final if nothing was heard in 10 s of audio.
           Audio arrives in ~560 ms model chunks, so the real pause is pause_ms plus up to ~0.6 s.

A new authenticated client replaces the current one (newest wins): after a
Wi-Fi drop the laptop's old connection may look alive for a while, and the
reconnect must not be locked out by it. The old one is closed with 4002.
"""

import argparse
import asyncio
import json
import logging
import re
import os
import secrets
import time
import urllib.parse
import sys
from pathlib import Path

import numpy as np
import sherpa_onnx
import websockets
from websockets.asyncio.server import serve
import websockets.http11 as _h11

# Kõnele sends its audio format in the URL with raw spaces
# ("?content-type=audio/x-raw, layout=(string)interleaved, rate=(int)16000, ..."),
# which strict HTTP parsing rejects. Escape spaces inside the request line's path.
_orig_parse_line = _h11.parse_line


def _lenient_parse_line(read_line, *args, **kwargs):
    line = yield from _orig_parse_line(read_line, *args, **kwargs)
    if line.startswith(b"GET ") and line.count(b" ") > 2:
        head, _, proto = bytes(line).rpartition(b" ")
        method, _, path = head.partition(b" ")
        line = method + b" " + path.replace(b" ", b"%20") + b" " + proto
    return line


_h11.parse_line = _lenient_parse_line

SAMPLE_RATE = 16000
DEFAULT_MODEL_DIR = Path.home() / "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25"
TOKEN_FILE = Path.home() / ".dictation_relay_token"

log = logging.getLogger("relay")


def load_token(cli_token: str | None) -> str:
    """CLI arg > env var > token file > generate and save a new one."""
    tok = cli_token or os.environ.get("RELAY_TOKEN")
    if tok:
        return tok.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    tok = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(tok + "\n")
    TOKEN_FILE.chmod(0o600)
    log.info("Generated new token and saved to %s", TOKEN_FILE)
    return tok


def token_hint(token: str, cli_token: str | None = None) -> str:
    """Log-safe description of the token: where it lives + last 4 chars. Never the full value."""
    where = "--token/$RELAY_TOKEN" if (cli_token or os.environ.get("RELAY_TOKEN")) else str(TOKEN_FILE)
    return "Token in %s (ends ...%s)" % (where, token[-4:])


def build_recognizer(model_dir: Path, threads: int) -> sherpa_onnx.OnlineRecognizer:
    def f(name):
        p = model_dir / name
        if not p.exists():
            raise SystemExit(f"Missing model file: {p}")
        return str(p)

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f("tokens.txt"),
        encoder=f("encoder.int8.onnx"),
        decoder=f("decoder.int8.onnx"),
        joiner=f("joiner.int8.onnx"),
        num_threads=threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
    )


def result_text(recognizer, stream) -> str:
    r = recognizer.get_result(stream)
    return (r if isinstance(r, str) else getattr(r, "text", "")).strip()


class PauseDetector:
    """Auto-stop logic for the keyboard endpoint, measured in AUDIO time (samples received).

    Text-stability method: once the hypothesis is non-empty, if it has not changed for
    pause_ms of audio, the speaker has paused. Needs no recognizer endpoint config, so the
    laptop /ws decoding is untouched. The model emits text in ~560 ms chunks, so the effective
    pause is pause_ms plus up to one chunk. If no text appears in no_speech_ms of audio the
    user never spoke. update() returns None (keep going), "pause" or "no_speech".
    """

    MIN_PAUSE_MS, MAX_PAUSE_MS, DEFAULT_PAUSE_MS = 400, 5000, 1200
    NO_SPEECH_MS = 10_000

    def __init__(self, pause_ms=None, rate=SAMPLE_RATE):
        try:
            p = int(pause_ms) if pause_ms is not None else self.DEFAULT_PAUSE_MS
        except (TypeError, ValueError):
            p = self.DEFAULT_PAUSE_MS
        self.pause_ms = min(max(p, self.MIN_PAUSE_MS), self.MAX_PAUSE_MS)
        self.pause_samples = self.pause_ms * rate // 1000
        self.no_speech_samples = self.NO_SPEECH_MS * rate // 1000
        self.last_text = ""
        self.last_change = 0     # total samples when the text last changed

    @classmethod
    def from_query(cls, query):
        """Detector if the URL asks for auto_stop=1, else None (today's manual-EOS behaviour)."""
        if (query.get("auto_stop") or [""])[0] != "1":
            return None
        return cls((query.get("pause_ms") or [None])[0])

    def update(self, total_samples: int, text: str):
        if text != self.last_text:
            self.last_text = text
            self.last_change = total_samples
        if not text:
            return "no_speech" if total_samples >= self.no_speech_samples else None
        if total_samples - self.last_change >= self.pause_samples:
            return "pause"
        return None


class Utterance:
    """One dictation: a recognizer stream plus timing stats."""

    def __init__(self, recognizer):
        self.rec = recognizer
        self.stream = recognizer.create_stream()
        self.samples = 0
        self.decode_s = 0.0
        self.last_text = ""

    def feed(self, pcm: bytes) -> str | None:
        """Feed audio, decode what's ready. Returns new text if it changed."""
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self.samples += audio.size
        self.stream.accept_waveform(SAMPLE_RATE, audio)
        return self._decode()

    def finish(self, tail_pad_s: float) -> str:
        pad = np.zeros(int(tail_pad_s * SAMPLE_RATE), dtype=np.float32)
        self.stream.accept_waveform(SAMPLE_RATE, pad)
        self.stream.input_finished()
        self._decode()
        return result_text(self.rec, self.stream)

    def _decode(self) -> str | None:
        t0 = time.perf_counter()
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)
        self.decode_s += time.perf_counter() - t0
        text = result_text(self.rec, self.stream)
        if text != self.last_text:
            self.last_text = text
            return text
        return None

    @property
    def audio_s(self) -> float:
        return self.samples / SAMPLE_RATE


class RelayServer:
    def __init__(self, recognizer, token: str, tail_pad_s: float):
        self.rec = recognizer
        self.token = token
        self.tail_pad_s = tail_pad_s
        self.busy = asyncio.Lock()
        self.current = None      # the connected client's websocket
        self.decode_lock = asyncio.Lock()   # laptop + phone keyboard can share the model
        self.kaldi_count = 0

    async def run_blocking(self, fn, *args):
        # Decoding is CPU-bound; run it off the event loop so heartbeats and
        # frame reads keep flowing. One decode at a time across all clients.
        async with self.decode_lock:
            return await asyncio.to_thread(fn, *args)

    async def handler(self, ws):
        peer = ws.remote_address[0] if ws.remote_address else "?"
        url = urllib.parse.urlsplit(ws.request.path)
        if url.path == "/client/ws/speech":
            return await self.kaldi_speech(ws, peer, urllib.parse.parse_qs(url.query))
        if url.path == "/client/ws/status":
            return await self.kaldi_status(ws)
        if url.path != "/ws":
            await ws.close(4004, "not found")
            return

        # --- auth ---
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=3)
            msg = json.loads(first) if isinstance(first, str) else {}
        except (asyncio.TimeoutError, json.JSONDecodeError, websockets.ConnectionClosed):
            msg = {}
        if msg.get("type") != "auth" or not secrets.compare_digest(
            str(msg.get("token", "")), self.token
        ):
            log.warning("Rejected unauthenticated client %s", peer)
            await ws.close(4001, "unauthorized")
            return

        if self.current is not None:
            log.info("New client %s replaces the existing connection", peer)
            try:
                await self.current.close(4002, "replaced by newer connection")
            except Exception:  # noqa: BLE001
                pass

        async with self.busy:
            self.current = ws
            log.info("Client connected: %s", peer)
            await ws.send(json.dumps({"type": "ready", "sample_rate": SAMPLE_RATE, "format": "s16le"}))
            utt = None
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        if utt is None:
                            utt = Utterance(self.rec)
                        changed = await self.run_blocking(utt.feed, message)
                        if changed is not None:
                            await ws.send(json.dumps({"type": "partial", "text": changed}))
                        continue

                    cmd = message.strip()
                    opts = {}
                    if cmd.startswith("{"):
                        try:
                            opts = json.loads(cmd)
                            cmd = opts.get("type", "")
                        except json.JSONDecodeError:
                            cmd = ""

                    if cmd == "ping":
                        await ws.send(json.dumps({"type": "pong", "t": time.time()}))
                    elif cmd == "reset":
                        utt = None
                    elif cmd == "done":
                        if utt is None:
                            await ws.send(json.dumps({"type": "final", "text": "", "audio_s": 0, "tail_ms": 0, "rtf": 0}))
                            continue
                        t0 = time.perf_counter()
                        # Client may override the silence pad per utterance (clamped).
                        pad = opts.get("tail_pad", self.tail_pad_s)
                        try:
                            pad = min(max(float(pad), 0.1), 1.5)
                        except (TypeError, ValueError):
                            pad = self.tail_pad_s
                        text = await self.run_blocking(utt.finish, pad)
                        tail_ms = (time.perf_counter() - t0) * 1000
                        rtf = utt.decode_s / utt.audio_s if utt.audio_s else 0
                        await ws.send(json.dumps({
                            "type": "final", "text": text,
                            "audio_s": round(utt.audio_s, 2),
                            "tail_ms": round(tail_ms),
                            "rtf": round(rtf, 3),
                        }))
                        log.info("final  audio=%.2fs  tail=%dms  rtf=%.3f  text=%r",
                                 utt.audio_s, tail_ms, rtf, text[:80])
                        utt = None
                    else:
                        log.debug("Ignoring unknown command %r", cmd)
            except websockets.ConnectionClosed:
                pass
            finally:
                if self.current is ws:
                    self.current = None
                log.info("Client disconnected: %s", peer)


def _kaldi_msg(text, final):
    return json.dumps({"status": 0, "segment": 0, "id": "relay",
                       "result": {"hypotheses": [{"transcript": text}], "final": final}})


async def _kaldi_status(self, ws):
    await ws.send(json.dumps({"num_workers_available": 1, "num_requests_processed": self.kaldi_count}))
    try:
        await ws.wait_closed()
    except Exception:  # noqa: BLE001
        pass


async def _kaldi_speech(self, ws, peer, query):
    """kaldi-gstreamer-server compatible endpoint (for Kõnele)."""
    local = peer in ("127.0.0.1", "::1", "localhost")
    tok = (query.get("token") or [""])[0]
    if not local and not secrets.compare_digest(tok, self.token):
        log.warning("Rejected keyboard client %s (no/invalid token)", peer)
        await ws.send(json.dumps({"status": 1, "message": "unauthorized"}))
        await ws.close(4001, "unauthorized")
        return
    ctype = (query.get("content-type") or [""])[0]
    m = re.search(r"rate=\(int\)(\d+)", ctype)
    in_rate = int(m.group(1)) if m else SAMPLE_RATE
    if "S16LE" not in ctype and ctype:
        log.warning("Keyboard sent content-type %r; assuming S16LE", ctype)

    self.kaldi_count += 1
    pause = PauseDetector.from_query(query)
    log.info("Keyboard client connected: %s (rate %d)%s", peer, in_rate,
             f" auto_stop pause={pause.pause_ms}ms" if pause else "")
    utt = Utterance(self.rec)
    t0 = time.perf_counter()

    async def finish(why):
        t1 = time.perf_counter()
        text = await self.run_blocking(utt.finish, self.tail_pad_s)
        if TIDY is not None and text:
            try:
                text = TIDY(text)
            except Exception as e:  # noqa: BLE001
                log.warning("tidy failed: %s", e)
        await ws.send(_kaldi_msg(text, True))
        log.info("keyboard final (%s)  audio=%.2fs  tail=%dms  text=%r",
                 why, utt.audio_s, (time.perf_counter() - t1) * 1000, text[:80])
        await ws.close()

    try:
        async for message in ws:
            if isinstance(message, bytes):
                pcm = message
                if in_rate != SAMPLE_RATE:
                    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
                    n = int(round(a.size * SAMPLE_RATE / in_rate))
                    a = np.interp(np.linspace(0, a.size - 1, n), np.arange(a.size), a)
                    pcm = a.astype("<i2").tobytes()
                changed = await self.run_blocking(utt.feed, pcm)
                if changed is not None:
                    await ws.send(_kaldi_msg(changed, False))
                if pause is not None:
                    why = pause.update(utt.samples, utt.last_text)
                    if why:
                        await finish(why)
                        return
            elif message.strip() == "EOS":
                await finish("EOS")
                return
    except websockets.ConnectionClosed:
        pass
    finally:
        log.info("Keyboard client disconnected after %.1fs", time.perf_counter() - t0)


RelayServer.kaldi_speech = _kaldi_speech
RelayServer.kaldi_status = _kaldi_status

# Optional: same text tidy-up as the laptop (digits, times, final punctuation),
# if tidy.py + text2num are available on the phone.
# On the phone tidy.py is copied next to server.py (both into ~); in the repo it lives in ../shared.
sys.path.append(str(Path(__file__).resolve().parent.parent / "shared"))
try:
    from tidy import tidy as TIDY  # noqa: N812
except Exception:  # noqa: BLE001
    TIDY = None


async def main():
    ap = argparse.ArgumentParser(description="Dictation relay server (phone side)")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default all interfaces)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--tail-pad", type=float, default=0.7, help="seconds of silence added on 'done'")
    ap.add_argument("--token", help="shared secret (else $RELAY_TOKEN, else ~/.dictation_relay_token)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
    )
    token = load_token(args.token)

    t0 = time.perf_counter()
    rec = build_recognizer(args.model_dir, args.threads)
    # Warm-up: first decode is slower (allocations); do it now, not on the first dictation.
    warm = Utterance(rec)
    warm.feed((np.zeros(SAMPLE_RATE, dtype="<i2")).tobytes())
    warm.finish(0.3)
    log.info("Text tidy-up for keyboard: %s", "on" if TIDY else "off (tidy.py/text2num not installed)")
    log.info("Model loaded + warmed in %.2fs (threads=%d)", time.perf_counter() - t0, args.threads)

    server = RelayServer(rec, token, args.tail_pad)
    async with serve(server.handler, args.host, args.port, max_size=2**20,
                     ping_interval=10, ping_timeout=10):
        log.info("Listening on ws://%s:%d/ws", args.host, args.port)
        log.info("%s", token_hint(token, args.token))
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
