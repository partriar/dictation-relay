"""Settings for the laptop relay and its test clients: config.toml next to this file.

Precedence for every setting:  command-line flag  >  config.toml  >  built-in default.
Stdlib only (tomllib, Python 3.11+). No personal values live here: there is deliberately no
default phone address, so a missing `hosts` is a clear error rather than a silent guess.
"""
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.toml"

# name -> (built-in default, accepted type(s), allowed values or None)
# `hosts` has no default (empty list); `device` has none (None = system default mic).
SETTINGS = {
    "hosts":          ([], list, None),
    "port":           (8765, int, None),
    "keys":           ("caps", str, None),
    "device":         (None, (int, str), None),
    "insert":         ("paste", str, ("paste", "type")),
    "tail_pad":       (0.7, (int, float), None),
    "post_roll":      (0.0, (int, float), None),
    "trailing_space": (True, bool, None),
    "tidy":           (True, bool, None),
    "log_text":       (False, bool, None),
}


def defaults() -> dict:
    return {k: (list(v[0]) if isinstance(v[0], list) else v[0]) for k, v in SETTINGS.items()}


def _type_ok(val, typ) -> bool:
    types = typ if isinstance(typ, tuple) else (typ,)
    if isinstance(val, bool):          # True/False must not pass as numbers
        return bool in types
    return isinstance(val, types)


def load_config(path: Path | None = None) -> tuple[dict, list[tuple[str, str]]]:
    """Return (values, messages). values = built-in defaults overlaid with the file's valid keys.
    messages = [(level, text)] for the caller to log: missing file (info), unreadable file or
    unknown/bad keys (warning). Never raises for a bad config."""
    path = Path(path) if path else CONFIG_PATH
    values, msgs = defaults(), []
    if not path.exists():
        msgs.append(("info", f"No config file at {path}; using built-in defaults "
                             "(copy config.example.toml to config.toml)"))
        return values, msgs
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        msgs.append(("warning", f"Could not read {path} ({e}); using built-in defaults"))
        return values, msgs
    msgs.append(("info", f"Loaded config from {path}"))
    for key, val in raw.items():
        if key not in SETTINGS:
            msgs.append(("warning", f"config.toml: unknown key {key!r} ignored (known: {', '.join(SETTINGS)})"))
            continue
        _, typ, allowed = SETTINGS[key]
        ok = _type_ok(val, typ)
        if key == "hosts" and ok:
            ok = all(isinstance(h, str) and h.strip() for h in val)
        if ok and allowed is not None and val not in allowed:
            ok = False
        if not ok:
            msgs.append(("warning", f"config.toml: bad value for {key!r} ({val!r}); using the default"))
            continue
        values[key] = [h.strip() for h in val] if key == "hosts" else val
    return values, msgs


def split_hosts(s: str | None) -> list[str]:
    return [h.strip() for h in (s or "").split(",") if h.strip()]


def resolve(cli: dict, cfg: dict) -> dict:
    """cli: setting name -> value, None meaning 'flag not given'. Returns the effective settings."""
    out = dict(cfg)
    for k, v in cli.items():
        if v is not None:
            out[k] = v
    return out


NO_HOST_MSG = ("No phone address set. Put  hosts = [\"100.x.y.z\"]  in config.toml (copy "
               "config.example.toml) or pass --host.")


def client_target(cli_host: str | None, cli_port: int | None) -> tuple[str, int]:
    """For mic_client / wav_client: one host + port. --host wins, else the first host in config.toml."""
    cfg, msgs = load_config()
    for level, text in msgs:
        if level == "warning":
            print("warning:", text, file=sys.stderr)
    eff = resolve({"hosts": split_hosts(cli_host) or None, "port": cli_port}, cfg)
    if not eff["hosts"]:
        sys.exit(NO_HOST_MSG)
    return eff["hosts"][0], eff["port"]
