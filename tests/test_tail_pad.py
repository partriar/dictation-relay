"""Tail-pad experiment against the REAL model (needs sherpa_onnx + the model dir; not run by run_all.py).
Usage:  python tests/test_tail_pad.py [MODEL_DIR]     (or set DICTATION_MODEL_DIR)
MODEL_DIR must contain the sherpa-onnx model plus a test_wavs/ folder (0.wav, 1.wav, 8k.wav)."""
import os, sys, wave, time, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "phone"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "shared"))
md = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DICTATION_MODEL_DIR")
if not md: sys.exit("SKIP: pass the model dir or set DICTATION_MODEL_DIR")
import server
M=Path(md); rec=server.build_recognizer(M,4)
for name in ["0.wav","1.wav","8k.wav"]:
    w=wave.open(str(M/'test_wavs'/name)); sr=w.getframerate(); a=np.frombuffer(w.readframes(w.getnframes()),'<i2').astype(np.float32)
    if sr!=16000: a=np.interp(np.linspace(0,a.size-1,int(a.size*16000/sr)),np.arange(a.size),a)
    # trim trailing near-silence to simulate releasing the key right after the last word
    nz=np.where(np.abs(a)>800)[0]; a=a[:nz[-1]+800]
    pcm=a.astype('<i2').tobytes()
    for pad in (0.7,0.5,0.4,0.3,0.2):
        u=server.Utterance(rec)
        for i in range(0,len(pcm),3200): u.feed(pcm[i:i+3200])
        t=time.perf_counter(); txt=u.finish(pad); ms=(time.perf_counter()-t)*1000
        print(f"{name:7s} pad={pad} tail={ms:4.0f}ms  ...{txt[-45:]!r}")
