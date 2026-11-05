"""Combo hotkey (lctrl+lshift): toggle, hold, and cancel on another key / mouse click.
Asserts on outcomes. Run: python tests/test_relay_hotkeys.py"""
import sys, time, threading, types
import _common as C
import relay, pyperclip
from pynput import keyboard as K
import logging; logging.basicConfig(level=logging.INFO, format="   log: %(message)s")
PORT = C.free_port(); C.start_fake_server(PORT)
args = types.SimpleNamespace(host="127.0.0.1", port=PORT, token=C.TOKEN, keys="lctrl+lshift", device=None, insert="paste",
    tail_pad=0.7, post_roll=0.0, trailing_space=True, log_text=True, tidy=True)
r = relay.Relay(args, relay.pystray.Icon("x", None, "t")); threading.Thread(target=r.run_loop, daemon=True).start()
L = r.hotkey.listener; KD, KU = 0x100, 0x101; time.sleep(1.5)
PASTE = [("down", "ctrl"), ("press", "v"), ("release", "v"), ("up", "ctrl")]


def tap(): L.feed(KD, 0xA2); L.feed(KD, 0xA0); time.sleep(0.1); L.feed(KU, 0xA0); L.feed(KU, 0xA2)


def pasted_once(label):
    assert K.LOG == PASTE, f"{label}: expected exactly one Ctrl+V, got {K.LOG}"
    assert pyperclip.HIST and pyperclip.HIST[0].strip().lower().startswith("this is a test sentence"), \
        f"{label}: wrong text pasted: {pyperclip.HIST[:1]}"
    K.LOG.clear(); pyperclip.HIST.clear()


def nothing_pasted(label):
    assert r.state == "ready" and not r.recording, f"{label}: state={r.state} recording={r.recording}"
    assert K.LOG == [], f"{label}: should not paste, got {K.LOG}"


assert r.state == "ready", f"relay not ready at start: {r.state}"
print("--- 1. TOGGLE: tap, talk 4s, tap")
tap(); time.sleep(0.3); assert r.state == "recording", f"tap should start recording, state={r.state}"
time.sleep(4); tap(); time.sleep(2.5)
assert r.state == "ready"; pasted_once("toggle")

print("--- 2. HOLD 3s")
L.feed(KD, 0xA2); L.feed(KD, 0xA0); time.sleep(3); L.feed(KU, 0xA0); L.feed(KU, 0xA2); time.sleep(2.5)
assert r.state == "ready"; pasted_once("hold")

print("--- 3. Ctrl+Shift+T cancels")
L.feed(KD, 0xA2); L.feed(KD, 0xA0); time.sleep(0.05); L.feed(KD, 0x54); L.feed(KU, 0x54); L.feed(KU, 0xA0); L.feed(KU, 0xA2)
time.sleep(1); nothing_pasted("ctrl+shift+T")

print("--- 4. Ctrl+Shift+click cancels")
L.feed(KD, 0xA2); L.feed(KD, 0xA0); time.sleep(0.05); r.hotkey.mouse.cb(0, 0, "left", True); L.feed(KU, 0xA0); L.feed(KU, 0xA2)
time.sleep(1); nothing_pasted("ctrl+shift+click")

print("--- 5. toggle works again after cancels")
tap(); time.sleep(3); tap(); time.sleep(2.5)
assert r.state == "ready"; pasted_once("toggle after cancel")
print("PASS")
