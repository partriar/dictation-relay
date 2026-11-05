# /// script
# requires-python = ">=3.10"
# dependencies = ["pynput"]
# ///
"""
Side-by-side latency test: Handy vs dictation relay.

  uv run latency_test.py

A window opens with test sentences and a text box. Click into the box, then
dictate a sentence with Handy, then the same sentence with the relay
(Caps Lock (or Left Ctrl + Left Shift)). Each time text lands in the box, the window logs:

  latency = (text first appears) - (you released the hotkey)

Measured identically for both apps, from outside both apps.
Labelling is automatic: if Caps Lock (or Left Ctrl + Left Shift) were held together, it's
the relay; otherwise it's Handy. Results are also saved to latency-results.csv.
"""

import csv
import statistics
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont

from pynput import keyboard

HERE = Path(__file__).resolve().parent
CSV = HERE / "latency-results.csv"

SENTENCES = [
    "Inspection at 14 Elm Street, Riverdale is booked for Saturday at ten thirty.",
    "The vendor in Harbour Point wants offers above one point two million by Friday.",
    "Please call me back after two, I'm at an appraisal in Northfield until then.",
    "Technetium ninety nine m is the most common tracer used in bone scintigraphy.",
    "Long one: I spoke with the buyers this morning and they're keen, but they want a building "
    "and pest report before they commit. Can you send me the strata records for unit four, and "
    "let the owners know we'll have an answer by Thursday afternoon.",
]

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10
VK_LCTRL, VK_LSHIFT, VK_CAPS = 0xA2, 0xA0, 0x14


class KeyWatch:
    """Records the last *physical* key release and whether the relay combo was used."""

    def __init__(self):
        self.lock = threading.Lock()
        self.last_release = None
        self.held = set()
        self.relay_combo_seen = False
        self.listener = keyboard.Listener(win32_event_filter=self._f)
        self.listener.start()

    def _f(self, msg, data):
        if data.flags & LLKHF_INJECTED:      # ignore the apps' own Ctrl+V / typing
            return True
        with self.lock:
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self.held.add(data.vkCode)
                if {VK_LCTRL, VK_LSHIFT} <= self.held or VK_CAPS in self.held:
                    self.relay_combo_seen = True
            elif msg in (WM_KEYUP, WM_SYSKEYUP):
                self.held.discard(data.vkCode)
                self.last_release = time.perf_counter()
        return True

    def take(self):
        """Return (last_release, label) and reset the combo flag."""
        with self.lock:
            label = "relay" if self.relay_combo_seen else "handy"
            self.relay_combo_seen = False
            return self.last_release, label


class App:
    def __init__(self, root):
        self.root = root
        self.keys = KeyWatch()
        self.results: list[dict] = []
        self.pending = None           # (t_text, latency, label, start_index)
        self.sentence_i = 0

        root.title("Dictation latency test: Handy vs relay")
        root.geometry("900x640")
        big = tkfont.Font(size=13)

        top = tk.Frame(root, padx=12, pady=8)
        top.pack(fill="x")
        tk.Label(top, text="Say this (once with Handy, once with the relay):", anchor="w").pack(fill="x")
        self.sentence = tk.Label(top, font=tkfont.Font(size=14, weight="bold"), wraplength=860,
                                 justify="left", anchor="w")
        self.sentence.pack(fill="x", pady=(2, 6))
        nav = tk.Frame(top)
        nav.pack(fill="x")
        tk.Button(nav, text="◀ Prev sentence", command=lambda: self.step(-1)).pack(side="left")
        tk.Button(nav, text="Next sentence ▶", command=lambda: self.step(1)).pack(side="left", padx=6)
        tk.Button(nav, text="Clear box", command=self.clear).pack(side="left", padx=6)
        self.hint = tk.Label(nav, text="Click into the box below before dictating.", fg="#666")
        self.hint.pack(side="left", padx=12)

        self.box = tk.Text(root, height=8, font=big, wrap="word", padx=8, pady=8)
        self.box.pack(fill="both", expand=False, padx=12)
        self.box.bind("<<Modified>>", self.on_modified)

        tk.Label(root, text="Results", anchor="w", padx=12).pack(fill="x", pady=(10, 0))
        self.out = tk.Text(root, height=12, font=("Consolas", 10), state="disabled", padx=8, pady=6)
        self.out.pack(fill="both", expand=True, padx=12)
        self.summary = tk.Label(root, font=tkfont.Font(size=12, weight="bold"), anchor="w", padx=12, pady=8)
        self.summary.pack(fill="x")

        self.step(0)
        self.box.focus_set()

    def step(self, d):
        self.sentence_i = (self.sentence_i + d) % len(SENTENCES)
        self.sentence.config(text=f"{self.sentence_i + 1}/{len(SENTENCES)}  {SENTENCES[self.sentence_i]}")

    def clear(self):
        self.box.delete("1.0", "end")
        self.box.edit_modified(False)
        self.box.focus_set()

    def on_modified(self, _e):
        if not self.box.edit_modified():
            return
        self.box.edit_modified(False)
        now = time.perf_counter()
        if self.pending is None:
            t_rel, label = self.keys.take()
            if t_rel is None:
                return
            latency_ms = (now - t_rel) * 1000
            # Index where this dictation's text starts (approx: before the change).
            self.pending = {"t": now, "ms": latency_ms, "label": label,
                            "sentence": self.sentence_i + 1, "len0": len(self.box.get("1.0", "end-1c"))}
            # Typed (not pasted) text arrives in pieces; wait for it to settle.
            self.root.after(900, self.finalise)

    def finalise(self):
        p, self.pending = self.pending, None
        if not p:
            return
        full = self.box.get("1.0", "end-1c")
        p["text"] = full.strip().splitlines()[-1] if full.strip() else ""
        if p["ms"] > 15000:      # stale key release, not a real dictation
            return
        self.results.append(p)
        self.log(f"{p['label']:6s}  {p['ms']:6.0f} ms   s{p['sentence']}  {p['text'][-70:]}")
        self.box.insert("end", "\n")
        self.box.edit_modified(False)
        self.update_summary()
        new = not CSV.exists()
        with CSV.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "app", "latency_ms", "sentence", "text"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), p["label"], round(p["ms"]),
                        p["sentence"], p["text"]])

    def log(self, line):
        self.out.config(state="normal")
        self.out.insert("end", line + "\n")
        self.out.see("end")
        self.out.config(state="disabled")

    def update_summary(self):
        parts = []
        for app in ("handy", "relay"):
            xs = [r["ms"] for r in self.results if r["label"] == app]
            if xs:
                parts.append(f"{app}: median {statistics.median(xs):.0f} ms, worst {max(xs):.0f} ms (n={len(xs)})")
        h = [r["ms"] for r in self.results if r["label"] == "handy"]
        r = [r["ms"] for r in self.results if r["label"] == "relay"]
        if h and r:
            parts.append(f"→ relay is {statistics.median(h) / statistics.median(r):.1f}× faster")
        self.summary.config(text="    ".join(parts))


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
