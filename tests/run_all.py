"""Run every stub-based test from anywhere:  python tests/run_all.py
Each test runs in its own process (they share global state). Needs: websockets numpy pillow text2num
(e.g. `uv run --with websockets --with numpy --with pillow --with text2num tests/run_all.py`).
test_tail_pad.py needs the real model and is skipped unless DICTATION_MODEL_DIR is set."""
import os, subprocess, sys
from pathlib import Path

T = Path(__file__).resolve().parent
tests = ["test_config.py", "test_multi_host.py", "test_relay_caps.py", "test_relay_hotkeys.py", "test_pause_detector.py", "test_token_log.py"]
if os.environ.get("DICTATION_MODEL_DIR"):
    tests.append("test_tail_pad.py")
else:
    print("SKIP test_tail_pad.py (needs the real model: set DICTATION_MODEL_DIR)")
bad = 0
for t in tests:
    print(f"=== {t}", flush=True)
    r = subprocess.run([sys.executable, str(T / t)], cwd=T.parent)
    print(f"=== {t}: {'ok' if r.returncode == 0 else 'FAILED (exit %d)' % r.returncode}", flush=True)
    bad += r.returncode != 0
sys.exit(1 if bad else 0)
