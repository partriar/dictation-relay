"""Caps Lock hotkey: tap = toggle, hold = push-to-talk, Shift+Caps passes through to Windows.
Asserts on outcomes (what Windows sees, what got pasted). Run: python tests/test_relay_caps.py"""
import sys, time, threading, types
import _common as C
import relay, pyperclip
SHIFT = [False]; relay.shift_down = lambda: SHIFT[0]
from pynput import keyboard as K
import logging; logging.basicConfig(level=logging.INFO, format="   log: %(message)s")
PORT = C.free_port(); C.start_fake_server(PORT)
args = types.SimpleNamespace(host="127.0.0.1", port=PORT, token=C.TOKEN, keys="caps", device=None, insert="paste",
    tail_pad=0.7, post_roll=0.0, trailing_space=True, log_text=True, tidy=True)
r = relay.Relay(args, relay.pystray.Icon("x", None, "t")); threading.Thread(target=r.run_loop, daemon=True).start()
L = r.hotkey.listener; KD, KU = 0x100, 0x101; time.sleep(1.5)
PASTE = [("down", "ctrl"), ("press", "v"), ("release", "v"), ("up", "ctrl")]


def pasted_once(label):
    assert K.LOG == PASTE, f"{label}: expected exactly one Ctrl+V, got {K.LOG}"
    assert len(pyperclip.HIST) >= 1 and pyperclip.HIST[0].strip().lower().startswith("this is a test sentence"), \
        f"{label}: wrong text pasted: {pyperclip.HIST[:1]}"
    K.LOG.clear(); pyperclip.HIST.clear()


assert r.state == "ready", f"relay not ready at start: {r.state}"
print("--- tap caps (toggle)")
assert L.feed(KD, 0x14) == "SUPPRESSED" and L.feed(KU, 0x14) == "SUPPRESSED", "Caps Lock must be hidden from Windows"
time.sleep(0.5); assert r.recording and r.toggle, "tap should start toggle recording"
time.sleep(2.5)
assert L.feed(KD, 0x14) == "SUPPRESSED" and L.feed(KU, 0x14) == "SUPPRESSED"
time.sleep(2.5); assert not r.recording and r.state == "ready"
pasted_once("toggle")

print("--- hold caps 3s")
assert L.feed(KD, 0x14) == "SUPPRESSED"; time.sleep(1)
assert L.feed(KD, 0x14) == "SUPPRESSED", "key repeat must stay hidden"
time.sleep(2); L.feed(KU, 0x14); time.sleep(2.5)
assert not r.recording and r.state == "ready"
pasted_once("hold")

print("--- shift+caps passes to Windows")
SHIFT[0] = True
assert L.feed(KD, 0x14) == "pass" and L.feed(KU, 0x14) == "pass", "Shift+Caps must reach Windows"
SHIFT[0] = False; time.sleep(1)
assert not r.recording and r.state == "ready" and K.LOG == [], f"Shift+Caps must not dictate: {r.state} {K.LOG}"
print("PASS")
