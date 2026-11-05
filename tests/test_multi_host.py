"""Relay with several hosts: one dead address plus the fake phone. First to answer must win.
Run from repo root:  python tests/test_multi_host.py   (needs websockets, numpy, pillow, text2num)"""
import sys, time, threading, types
import _common as C
import relay
import logging; logging.basicConfig(level=logging.INFO, format="   log: %(message)s")

port = C.free_port(); C.start_fake_server(port)
hosts = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.1,127.0.0.1"   # 192.0.2.1 = TEST-NET, never answers
args = types.SimpleNamespace(host=hosts, port=port, token=C.TOKEN, keys="caps", device=None, insert="paste",
    tail_pad=0.7, post_roll=0.0, trailing_space=True, log_text=True, tidy=True)
r = relay.Relay(args, relay.pystray.Icon("x", None, "t")); threading.Thread(target=r.run_loop, daemon=True).start()
t = time.time()
while r.state != "ready" and time.time() - t < 3: time.sleep(0.05)
print(f"{hosts}: state={r.state} after {time.time()-t:.2f}s url={r.url}")
assert r.state == "ready", "relay never reached ready"
assert "127.0.0.1" in r.url, "wrong host won the race"
print("PASS")
