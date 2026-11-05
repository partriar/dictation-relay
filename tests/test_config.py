"""config.toml loading + precedence (CLI flag > config.toml > built-in default). No relay start needed.
Run: python tests/test_config.py"""
import tempfile
from pathlib import Path
import _common as C
import relay_config as rc

tmp = Path(tempfile.mkdtemp())
D = rc.defaults()
assert D["hosts"] == [] and D["port"] == 8765 and D["keys"] == "caps" and D["tail_pad"] == 0.7 and D["tidy"] is True, D

# missing file -> defaults + an info line, no crash
cfg, msgs = rc.load_config(tmp / "nope.toml")
assert cfg == D and [m[0] for m in msgs] == ["info"] and "No config file" in msgs[0][1], msgs

# file overrides defaults
f = tmp / "c.toml"
f.write_text('hosts = ["192.168.1.50", "100.1.2.3"]\nport = 9000\nkeys = "lctrl+lshift"\ntail_pad = 0.9\ntidy = false\n')
cfg, msgs = rc.load_config(f)
assert cfg["hosts"] == ["192.168.1.50", "100.1.2.3"] and cfg["port"] == 9000 and cfg["tidy"] is False and cfg["tail_pad"] == 0.9
assert cfg["insert"] == "paste", "unset keys keep the default"
assert all(m[0] == "info" for m in msgs), msgs

# CLI beats file; unset CLI (None) falls through to file, then default
eff = rc.resolve({"hosts": rc.split_hosts("9.9.9.9"), "port": None, "tidy": True, "keys": None}, cfg)
assert eff["hosts"] == ["9.9.9.9"] and eff["port"] == 9000 and eff["tidy"] is True and eff["keys"] == "lctrl+lshift"
assert rc.resolve({"port": 1}, D)["port"] == 1 and rc.resolve({}, D)["port"] == 8765

# unknown key -> warning naming it; bad types/values -> warning, default kept
f.write_text('hosts = ["1.2.3.4"]\nbogus_key = 1\nport = "x"\ninsert = "shout"\ntidy = 1\n')
cfg, msgs = rc.load_config(f)
warns = [m[1] for m in msgs if m[0] == "warning"]
assert any("'bogus_key'" in w for w in warns), warns
assert any("'port'" in w for w in warns) and any("'insert'" in w for w in warns) and any("'tidy'" in w for w in warns), warns
assert cfg["hosts"] == ["1.2.3.4"] and cfg["port"] == 8765 and cfg["insert"] == "paste" and cfg["tidy"] is True

# broken TOML -> warning + defaults, no crash
f.write_text("this is = = not toml")
cfg, msgs = rc.load_config(f)
assert cfg == D and msgs[0][0] == "warning", msgs

# clients: --host wins; no host anywhere -> clear exit
assert rc.client_target("5.5.5.5", 7000) == ("5.5.5.5", 7000)
orig = rc.load_config
rc.load_config = lambda path=None: orig(tmp / "absent.toml")
try:
    rc.client_target(None, None)
    raise AssertionError("expected exit")
except SystemExit as e:
    assert "config.toml" in str(e.code), e.code
print("PASS")
