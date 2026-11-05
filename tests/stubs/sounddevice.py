# fake mic: plays a wav at real speed into the callback; device native 48k to test resampling
import threading, time, wave, numpy as np, os
WAV=os.environ["FAKE_WAV"]
class _D: device=[0,0]
default=_D()
def query_devices(dev=None, kind=None):
    d={"name":"Fake mic","max_input_channels":1,"default_samplerate":48000}
    return d if kind else [d]
class InputStream:
    def __init__(self, samplerate, channels, dtype, blocksize, device, callback):
        if samplerate==16000 and os.environ.get("FAKE_REFUSE16")=="1": raise RuntimeError("Invalid sample rate")
        self.sr=samplerate; self.bs=blocksize; self.cb=callback; self.run=False
        w=wave.open(WAV); a=np.frombuffer(w.readframes(w.getnframes()),dtype="<i2").astype(np.float32)/32768; sr=w.getframerate()
        n=int(a.size*samplerate/sr); self.a=np.interp(np.linspace(0,a.size-1,n),np.arange(a.size),a).astype(np.float32)
    def start(self):
        self.run=True; threading.Thread(target=self._go,daemon=True).start()
    def _go(self):
        i=0
        while self.run:
            blk=self.a[i:i+self.bs]
            if blk.size<self.bs: blk=np.concatenate([blk,np.zeros(self.bs-blk.size,np.float32)])
            self.cb(blk.reshape(-1,1),self.bs,None,None); i+=self.bs
            time.sleep(self.bs/self.sr)
    def stop(self): self.run=False
    def close(self): pass
