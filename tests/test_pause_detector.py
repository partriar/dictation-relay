"""PauseDetector (WP7 auto-stop) logic. No model needed: sherpa_onnx is stubbed.
Run from repo root:  python tests/test_pause_detector.py   (needs websockets, numpy)"""
import sys, types
from pathlib import Path

_so = types.ModuleType("sherpa_onnx"); _so.OnlineRecognizer = object
sys.modules.setdefault("sherpa_onnx", _so)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "phone"))
import server
from server import PauseDetector

R = 16  # samples per ms


def run(det, steps):
    """steps: list of (ms_of_audio, text). Returns (index, reason) of first trigger, else None."""
    total = 0
    for i, (ms, text) in enumerate(steps):
        total += ms * R
        why = det.update(total, text)
        if why:
            return i, why
    return None


# absent / other params = disabled (today's behaviour)
assert PauseDetector.from_query({}) is None
assert PauseDetector.from_query({"pause_ms": ["800"]}) is None
assert PauseDetector.from_query({"auto_stop": ["0"]}) is None
assert PauseDetector.from_query({"auto_stop": ["yes"]}) is None
d = PauseDetector.from_query({"auto_stop": ["1"]})
assert d is not None and d.pause_ms == 1200            # default

# clamp 400..5000, junk falls back to default
assert PauseDetector.from_query({"auto_stop": ["1"], "pause_ms": ["10"]}).pause_ms == 400
assert PauseDetector.from_query({"auto_stop": ["1"], "pause_ms": ["99999"]}).pause_ms == 5000
assert PauseDetector.from_query({"auto_stop": ["1"], "pause_ms": ["abc"]}).pause_ms == 1200
assert PauseDetector.from_query({"auto_stop": ["1"], "pause_ms": ["2500"]}).pause_ms == 2500

# no trigger without text (silence < 10 s)
assert run(PauseDetector(1200), [(560, "")] * 17) is None            # 9.5 s of nothing

# 10 s with no text -> no_speech
assert run(PauseDetector(1200), [(560, "")] * 18) == (17, "no_speech")  # 10.08 s

# trigger after pause: text appears at 560, then stable chunks of 560 ms
# stable for 560, 1120 -> no; 1680 >= 1200 -> trigger on 3rd stable chunk
steps = [(560, "hello")] + [(560, "hello")] * 3
assert run(PauseDetector(1200), steps) == (3, "pause")
assert run(PauseDetector(1200), steps[:3]) is None
# exactly at threshold counts (pause 1120 = 2 chunks)
assert run(PauseDetector(1120), [(560, "hi"), (560, "hi"), (560, "hi")]) == (2, "pause")

# text change resets the timer
steps = [(560, "hello"), (560, "hello"), (560, "hello world"), (560, "hello world"),
         (560, "hello world"), (560, "hello world"), (560, "hello world")]
assert run(PauseDetector(1200), steps[:5]) is None          # change at idx 2, only 1120 ms stable by idx 4
assert run(PauseDetector(1200), steps) == (5, "pause")      # 1680 ms stable at idx 5

# text that keeps changing never triggers
assert run(PauseDetector(1200), [(560, "a" * (i + 1)) for i in range(40)]) is None

# speech then silence: no_speech must not fire once text exists
d = PauseDetector(5000)
assert d.update(100 * R, "hi") is None
assert d.update(11_000 * R, "hi") == "pause"   # pause (not no_speech) once text exists

# end-to-end handler wiring with a fake recognizer is not possible without the model;
# check the handler source accepts the detector and shares one finish path with EOS.
import inspect
src = inspect.getsource(server._kaldi_speech)
assert "PauseDetector.from_query(query)" in src and src.count("await finish(") == 2
print("PASS")
