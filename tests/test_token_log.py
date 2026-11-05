"""Token must never be logged in full. sherpa_onnx stubbed. Run: python tests/test_token_log.py"""
import os, sys, tempfile, types
from pathlib import Path

_so = types.ModuleType("sherpa_onnx"); _so.OnlineRecognizer = object
sys.modules.setdefault("sherpa_onnx", _so)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "phone"))
import server

os.environ.pop("RELAY_TOKEN", None)
server.TOKEN_FILE = Path(tempfile.mkdtemp()) / "tok"
tok = server.load_token(None)               # generates + saves
assert server.TOKEN_FILE.read_text().strip() == tok and len(tok) >= 32
hint = server.token_hint(tok)
assert tok not in hint and hint.endswith("...%s)" % tok[-4:]) and str(server.TOKEN_FILE) in hint
assert server.load_token(None) == tok       # reload from file
assert 'log.info("Token: %s"' not in Path(server.__file__).read_text(encoding="utf-8")
print("token log tests OK")
