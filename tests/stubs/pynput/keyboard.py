import contextlib
LOG=[]
class _Suppress(Exception): pass
class Listener:
    def __init__(self, win32_event_filter=None, **k): self.f=win32_event_filter
    def start(self): pass
    def stop(self): pass
    def suppress_event(self): raise _Suppress()
    def feed(self, msg, vk, injected=False):
        class D: pass
        d=D(); d.vkCode=vk; d.flags=0x10 if injected else 0
        try: self.f(msg,d); return "pass"
        except _Suppress: return "SUPPRESSED"
class Key: ctrl="ctrl"
class Controller:
    @contextlib.contextmanager
    def pressed(self,k): LOG.append(("down",k)); yield; LOG.append(("up",k))
    def press(self,k): LOG.append(("press",k))
    def release(self,k): LOG.append(("release",k))
    def type(self,t): LOG.append(("type",t))
