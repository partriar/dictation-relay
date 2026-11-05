# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=13", "numpy", "sounddevice", "pynput", "pyperclip", "pystray", "pillow", "text2num>=2.5"]
# ///
"""
Dictation relay - milestone 3: hold-to-talk dictation into any app (Windows).

Caps Lock (default; Shift+Caps Lock still toggles capitals), two ways:
  tap  -> start recording, tap again -> stop and paste
  hold -> record while held, release -> paste
With a combo like --keys lctrl+lshift, pressing any other key or clicking while
holding them (e.g. Ctrl+Shift+T) cancels, so normal shortcuts still work. Only one copy can run at a time.
The text is pasted at the cursor.

  uv run --gui-script relay.py          # no console window, tray icon only
  uv run relay.py                       # same, but with a console showing logs
  uv run relay.py --show-keys           # print key codes as you press keys, to check the hotkey

Tray icon colours:  grey = ready   red = recording   amber = waiting for text   black = phone offline
Right-click the tray icon to quit or open the log.

Settings: config.toml next to this script (see config.example.toml). Precedence: CLI flag > config.toml > default.
Token: --token, or RELAY_TOKEN env var, or a file named token.txt next to this script.
Log:   relay.log next to this script (latency per dictation; text is NOT logged unless --log-text).
"""

import argparse
import asyncio
import collections
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pyperclip
import pystray
import sounddevice as sd
import websockets
from PIL import Image, ImageDraw
from pynput import keyboard, mouse

HERE = Path(__file__).resolve().parent
# tidy.py has one source of truth: ../shared/tidy.py in the repo. A copy sitting next to this
# script (if someone drops one there) is found first; the repo copy is the fallback.
sys.path.append(str(HERE.parent / "shared"))
from tidy import tidy  # noqa: E402
import relay_config  # noqa: E402
SR = 16000
FRAME_MS = 100
PREROLL_FRAMES = 3          # 300 ms of audio from just before the key press
CONNECT_TIMEOUT = 1.0       # brief: show "offline" within ~1 s
TAP_MAX = 0.3               # shorter than this = a tap (toggle mode), longer = hold-to-talk
TOGGLE_MAX_S = 120          # toggle recording auto-stops after this long

# Windows virtual-key codes
VK = {
    "rctrl": {0xA3}, "lctrl": {0xA2}, "ctrl": {0xA2, 0xA3, 0x11},
    "rwin": {0x5C}, "lwin": {0x5B}, "win": {0x5B, 0x5C},
    "ralt": {0xA5}, "lalt": {0xA4}, "rshift": {0xA1}, "lshift": {0xA0},
    "caps": {0x14},
    "f13": {0x7C}, "f14": {0x7D}, "f15": {0x7E},
}
WIN_VKS = {0x5B, 0x5C}
VK_CAPS = 0x14


def shift_down() -> bool:
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10

log = logging.getLogger("relay")


# ---------------------------------------------------------------- tray icon
def make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color, outline="white", width=3)
    # little mic glyph
    d.rounded_rectangle((26, 16, 38, 38), radius=6, fill="white")
    d.line((32, 38, 32, 47), fill="white", width=3)
    d.line((24, 47, 40, 47), fill="white", width=3)
    return img


ICONS = {
    "ready": ("#6b7280", "Dictation relay: ready"),
    "recording": ("#dc2626", "Dictation relay: recording"),
    "waiting": ("#d97706", "Dictation relay: transcribing"),
    "offline": ("#111111", "Dictation relay: phone offline"),
}


# ---------------------------------------------------------------- hotkey
class Hotkey:
    """
    Low-level keyboard hook. Fires on_down when every key in the combo is held,
    on_up when any of them is released.

    The Windows key is special: on its own, releasing it opens the Start menu,
    and Ctrl+Win+<x> are system shortcuts. So once the non-Win part of the
    combo is held, Win presses are swallowed (Windows never sees them).
    """

    def __init__(self, combo: list[str], on_down, on_up, on_cancel):
        self.groups = [VK[k] for k in combo]
        self.on_down, self.on_up, self.on_cancel = on_down, on_up, on_cancel
        self.cancelled = False
        self.held: set[int] = set()
        self.swallowed: set[int] = set()
        self.active = False
        self.listener = keyboard.Listener(win32_event_filter=self._filter)
        # Ctrl+Shift+click (range select etc.) is also a shortcut, not dictation.
        self.mouse = mouse.Listener(on_click=self._click)

    def start(self):
        self.listener.start()
        self.mouse.start()

    def stop(self):
        self.listener.stop()
        self.mouse.stop()

    def _click(self, x, y, button, pressed):
        if pressed and self.active and not self.cancelled:
            self.cancelled = True
            self.on_cancel()

    def _in_combo(self, vk):
        return any(vk in g for g in self.groups)

    def _all_held(self):
        return all(self.held & g for g in self.groups)

    def _filter(self, msg, data):
        if data.flags & LLKHF_INJECTED:          # ignore our own Ctrl+V
            return True
        vk = data.vkCode
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if not self._in_combo(vk):
            # Another key while the combo is held = it's a shortcut (Ctrl+Shift+T etc.), not dictation.
            if down and self.active and not self.cancelled:
                self.cancelled = True
                self.on_cancel()
            return True

        if down:
            # Shift+Caps Lock keeps its normal job (toggle capitals).
            if vk == VK_CAPS and vk not in self.held and shift_down():
                return True
            self.held.add(vk)
            non_win_held = all(self.held & g for g in self.groups if not (g & WIN_VKS))
            self._maybe_fire()
            if vk == VK_CAPS or (vk in WIN_VKS and non_win_held):
                self.swallowed.add(vk)
                # suppress_event() raises internally to stop the event, so it must come last.
                self.listener.suppress_event()   # Windows never sees this Win press
        else:
            if vk not in self.held:        # e.g. the Shift+Caps release we passed through
                return True
            self.held.discard(vk)
            if self.active and not self._all_held():
                self.active = False
                if not self.cancelled:
                    self.on_up()
            if vk in self.swallowed:
                self.swallowed.discard(vk)
                self.listener.suppress_event()   # matching release, also hidden
        return True

    def _maybe_fire(self):
        if not self.active and self._all_held():
            self.active = True
            self.cancelled = False
            self.on_down()

    def all_released(self):
        return not (self.held & set().union(*self.groups))


# ---------------------------------------------------------------- text insertion
_kb = keyboard.Controller()


def insert_text(text: str, mode: str) -> float:
    """Insert text at the cursor. Returns the moment the text was delivered."""
    if not text:
        return time.perf_counter()
    if mode == "type":
        _kb.type(text)
        return time.perf_counter()
    try:
        previous = pyperclip.paste()
    except Exception:  # noqa: BLE001
        previous = None
    pyperclip.copy(text)
    with _kb.pressed(keyboard.Key.ctrl):
        _kb.press("v")
        _kb.release("v")
    delivered = time.perf_counter()
    # Give the target app time to read the clipboard before restoring it.
    time.sleep(0.25)
    try:
        if previous and pyperclip.paste() == text:
            pyperclip.copy(previous)
    except Exception:  # noqa: BLE001
        pass
    return delivered


# ---------------------------------------------------------------- the app
class Relay:
    def __init__(self, args, icon: pystray.Icon):
        self.args = args
        self.icon = icon
        # Several addresses for the phone, tried at the same time; first to answer wins.
        self.urls = [f"ws://{h}:{args.port}/ws" for h in relay_config.split_hosts(args.host)]
        self.url = self.urls[0]
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self.recording = False
        self.preroll: collections.deque[bytes] = collections.deque(maxlen=PREROLL_FRAMES)
        self.outq: asyncio.Queue | None = None
        self.final_fut: asyncio.Future | None = None
        self.t_release = 0.0
        self.hotkey = Hotkey(args.keys.split("+"), self._hk_down, self._hk_up, self._hk_cancel)
        self.t_press = 0.0
        self.toggle = False
        self.ignore_next_up = False
        self.state = None

    # ---- UI
    def set_state(self, s):
        if s == self.state:
            return
        self.state = s
        color, title = ICONS[s]
        self.icon.icon = make_icon(color)
        self.icon.title = title

    def beep(self):
        try:
            import winsound
            winsound.Beep(300, 120)
        except Exception:  # noqa: BLE001
            pass

    # ---- threads -> loop
    def _hk_down(self):
        t = time.perf_counter()
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.on_press(t)))

    def _hk_cancel(self):
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.cancel("other key pressed")))

    def _hk_up(self):
        t = time.perf_counter()
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.on_release(t)))

    def _mic_cb(self, indata, frames, time_info, status):
        a = indata[:, 0].astype(np.float32)
        if self.in_rate != SR:
            n = int(round(a.size * SR / self.in_rate))
            a = np.interp(np.linspace(0, a.size - 1, n), np.arange(a.size), a).astype(np.float32)
        pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
        self.loop.call_soon_threadsafe(self._on_frame, pcm)

    def _on_frame(self, pcm):
        if self.recording:
            self.outq.put_nowait(pcm)
        else:
            self.preroll.append(pcm)

    # ---- mic
    def open_mic(self):
        dev = self.args.device
        for rate in (SR, None):
            try:
                self.in_rate = rate or int(sd.query_devices(dev, "input")["default_samplerate"])
                self.mic = sd.InputStream(samplerate=self.in_rate, channels=1, dtype="float32",
                                          blocksize=self.in_rate * FRAME_MS // 1000,
                                          device=dev, callback=self._mic_cb)
                self.mic.start()
                log.info("Mic open: %s @ %d Hz", sd.query_devices(dev, "input")["name"], self.in_rate)
                return
            except Exception as e:  # noqa: BLE001
                if rate is None:
                    raise
                log.info("Mic refused 16 kHz (%s); using native rate", e)

    # ---- connection
    async def _open(self, url):
        ws = await asyncio.wait_for(
            websockets.connect(url, max_size=2**20, ping_interval=10, ping_timeout=5,
                               proxy=None),  # never route the phone link via a system proxy
            timeout=CONNECT_TIMEOUT)
        return url, ws

    async def connect(self):
        # Race all addresses (Tailscale, LAN...). Only the winner authenticates,
        # so the phone never sees two clients and kicks one.
        tasks = [asyncio.ensure_future(self._open(u)) for u in self.urls]
        ws, errors = None, []
        for fut in asyncio.as_completed(tasks):
            try:
                url, ws = await fut
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}")
        for t in tasks:
            if not t.done():
                t.cancel()
            elif not t.cancelled() and t.exception() is None and t.result()[1] is not ws:
                await t.result()[1].close()
        if ws is None:
            raise ConnectionError("no address answered (" + ", ".join(errors) + ")")
        self.url = url
        await ws.send(json.dumps({"type": "auth", "token": self.args.token}))
        try:
            json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        except websockets.ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd else "?"
            raise ConnectionError(f"phone refused: {code}") from e
        self.ws = ws
        self.outq = asyncio.Queue()
        asyncio.ensure_future(self.reader(ws))
        asyncio.ensure_future(self.sender(ws, self.outq))
        log.info("Connected to %s", self.url)
        if not self.recording:
            self.set_state("ready")

    def connected(self):
        return self.ws is not None and self.ws.state.name == "OPEN"

    async def keep_connected(self):
        """Background: reconnect every few seconds so the tray shows real status."""
        while True:
            if not self.connected():
                try:
                    await self.connect()
                except Exception as e:  # noqa: BLE001
                    if self.state != "offline":
                        log.warning("Phone offline: %s %s", type(e).__name__, e)
                    self.set_state("offline")
            await asyncio.sleep(3)

    async def reader(self, ws):
        try:
            async for m in ws:
                msg = json.loads(m)
                if msg.get("type") == "final" and self.final_fut and not self.final_fut.done():
                    self.final_fut.set_result(msg)
        except websockets.ConnectionClosed:
            pass
        if self.final_fut and not self.final_fut.done():
            self.final_fut.set_exception(ConnectionError("connection lost"))
        self.set_state("offline")

    async def sender(self, ws, q):
        # Single sender keeps audio frames and the "done" marker in order.
        try:
            while True:
                item = await q.get()
                await ws.send(item)
        except websockets.ConnectionClosed:
            pass

    # ---- dictation
    async def on_press(self, t_press):
        # Second tap while in toggle mode = stop.
        if self.recording and self.toggle:
            self.ignore_next_up = True
            await self.finish(t_press)
            return
        if self.recording:
            return
        if not self.connected():
            try:
                await self.connect()
            except Exception as e:  # noqa: BLE001
                log.warning("Hotkey pressed but phone unreachable: %s %s", type(e).__name__, e)
                self.set_state("offline")
                self.beep()
                return
            if not self.hotkey.active:        # released while we were reconnecting
                return
        self.recording = True
        self.toggle = False
        self.t_press = time.perf_counter()
        self.final_fut = self.loop.create_future()
        for pcm in self.preroll:              # include the 300 ms before the press
            self.outq.put_nowait(pcm)
        self.preroll.clear()
        self.set_state("recording")

    async def cancel(self, why):
        if not self.recording:
            return
        self.recording = False
        self.toggle = False
        self.outq.put_nowait(json.dumps({"type": "reset"}))   # phone discards the audio
        self.set_state("ready")
        log.info("cancelled (%s)", why)

    async def on_release(self, t_release):
        if self.ignore_next_up:               # release of the tap that stopped toggle mode
            self.ignore_next_up = False
            return
        if not self.recording or self.toggle:
            return
        if t_release - self.t_press < TAP_MAX:
            # A tap: keep recording until the next tap.
            self.toggle = True
            log.info("toggle mode: tap again to stop")
            asyncio.ensure_future(self._toggle_timeout(self.t_press))
            return
        await self.finish(t_release)

    async def _toggle_timeout(self, started):
        await asyncio.sleep(TOGGLE_MAX_S)
        if self.recording and self.toggle and self.t_press == started:
            log.info("toggle recording hit %ds limit, stopping", TOGGLE_MAX_S)
            await self.finish(time.perf_counter())

    async def finish(self, t_release):
        self.toggle = False
        if self.args.post_roll > 0:
            await asyncio.sleep(self.args.post_roll)
        self.recording = False
        self.outq.put_nowait(json.dumps({"type": "done", "tail_pad": self.args.tail_pad}))
        self.set_state("waiting")
        try:
            final = await asyncio.wait_for(self.final_fut, timeout=5)
        except Exception as e:  # noqa: BLE001
            log.warning("No text back from phone: %s", e)
            self.set_state("offline" if not self.connected() else "ready")
            self.beep()
            return
        text = final.get("text", "").strip()
        raw = text
        if text and self.args.tidy:
            try:
                text = tidy(text)
            except Exception as e:  # noqa: BLE001 - never lose a dictation to formatting
                log.warning("tidy failed (%s); pasting raw text", e)
                text = raw
        if text and self.args.trailing_space:
            text += " "
        t_text = time.perf_counter()

        # Wait (briefly) until the hotkey is physically released, so our Ctrl+V
        # isn't mixed with a held Win/Ctrl.
        for _ in range(100):
            if self.hotkey.all_released():
                break
            await asyncio.sleep(0.01)
        t_done = await asyncio.to_thread(insert_text, text, self.args.insert)
        self.set_state("ready")
        log.info("dictation  audio=%.1fs  release->text=%dms  release->inserted=%dms  phone_tail=%sms  rtf=%s  chars=%d%s",
                 final.get("audio_s", 0), (t_text - t_release) * 1000, (t_done - t_release) * 1000,
                 final.get("tail_ms"), final.get("rtf"), len(text),
                 f"  raw={raw!r}  text={text!r}" if self.args.log_text else "")

    # ---- lifecycle
    def run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.open_mic()
        self.hotkey.start()
        self.loop.create_task(self.keep_connected())
        self.loop.run_forever()

    def shutdown(self):
        try:
            self.hotkey.stop()
            self.mic.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


def show_keys():
    print("Press keys to see their codes (Ctrl+C here to quit).")
    names = {v: k for k, vs in VK.items() for v in vs if len(vs) == 1}

    def f(msg, data):
        if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            print(f"  down vk=0x{data.vkCode:02X}  {names.get(data.vkCode, '')}")
        return True
    with keyboard.Listener(win32_event_filter=f) as lst:
        lst.join()


STARTUP_NAME = "dictation-relay.vbs"


def startup_path() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / STARTUP_NAME


def install_startup():
    """Put a hidden launcher in the user's Startup folder, then start it now."""
    import shutil
    uv = shutil.which("uv")
    if not uv:
        sys.exit("Can't find uv on PATH.")
    script = (
        "' Dictation relay: start hidden at login (tray icon only). Created by relay.py --install-startup\r\n"
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{HERE}"\r\n'
        f'sh.Run """{uv}"" run --gui-script ""{HERE / "relay.py"}""", 0, False\r\n'
    )
    dest = startup_path()
    dest.write_text(script, encoding="utf-8")
    print(f"Installed: {dest}")
    print("It will start automatically each time you log in. Starting it now...")
    os.startfile(dest)


def uninstall_startup():
    dest = startup_path()
    if dest.exists():
        dest.unlink()
        print(f"Removed: {dest}")
    else:
        print("Not installed.")


_mutex = None


def single_instance() -> bool:
    """Windows named mutex: False if another copy already holds it."""
    global _mutex
    if os.name != "nt":
        return True
    import ctypes
    k32 = ctypes.windll.kernel32
    _mutex = k32.CreateMutexW(None, False, "Local\\DictationRelaySingleInstance")
    return k32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def load_token(cli):
    if cli:
        return cli
    if os.environ.get("RELAY_TOKEN"):
        return os.environ["RELAY_TOKEN"]
    f = HERE / "token.txt"
    if f.exists():
        return f.read_text().strip()
    return None


def main():
    ap = argparse.ArgumentParser(description="Hold-to-talk dictation via the phone relay. "
                                 "Settings come from config.toml; flags below override it.")
    ap.add_argument("--config", type=Path, default=None, help="config file (default: config.toml next to relay.py)")
    ap.add_argument("--host", default=None,
                    help="phone address(es), comma-separated, tried in parallel (Tailscale, home Wi-Fi)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--token")
    ap.add_argument("--keys", default=None,
                    help=f"hotkey combo, '+'-separated. Names: {', '.join(VK)}")
    ap.add_argument("--device", type=lambda s: int(s) if s.isdigit() else s, default=None)
    ap.add_argument("--insert", choices=["paste", "type"], default=None,
                    help="paste (fast, default) or type (for apps that block paste)")
    ap.add_argument("--tail-pad", type=float, default=None,
                    help="silence the phone adds at the end (s). <0.7 clipped word endings in tests")
    ap.add_argument("--post-roll", type=float, default=None,
                    help="keep recording this long after release (s), if you release too early")
    ap.add_argument("--trailing-space", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--tidy", action=argparse.BooleanOptionalAction, default=None,
                    help="--no-tidy pastes raw model text (no digits/time/final punctuation clean-up)")
    ap.add_argument("--log-text", action=argparse.BooleanOptionalAction, default=None,
                    help="also write transcripts to relay.log")
    ap.add_argument("--show-keys", action="store_true")
    ap.add_argument("--install-startup", action="store_true", help="start automatically at login (and now)")
    ap.add_argument("--uninstall-startup", action="store_true", help="stop starting at login")
    args = ap.parse_args()

    if args.show_keys:
        show_keys()
        return
    if args.install_startup:
        install_startup()
        return
    if args.uninstall_startup:
        uninstall_startup()
        return

    handlers = [logging.FileHandler(HERE / "relay.log", encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    cfg, msgs = relay_config.load_config(args.config)
    for level, text in msgs:
        log.log(logging.WARNING if level == "warning" else logging.INFO, text)
    eff = relay_config.resolve(
        {"hosts": relay_config.split_hosts(args.host) or None, "port": args.port, "keys": args.keys,
         "device": args.device, "insert": args.insert, "tail_pad": args.tail_pad,
         "post_roll": args.post_roll, "trailing_space": args.trailing_space, "tidy": args.tidy,
         "log_text": args.log_text}, cfg)
    if not eff["hosts"]:
        log.error(relay_config.NO_HOST_MSG)
        sys.exit(1)
    args.host = ",".join(eff["hosts"])
    for k in ("port", "keys", "device", "insert", "tail_pad", "post_roll", "trailing_space", "tidy", "log_text"):
        setattr(args, k, eff[k])

    args.token = load_token(args.token)
    if not args.token:
        log.error("No token. Put it in token.txt next to relay.py (%s), or set RELAY_TOKEN.", HERE / "token.txt")
        sys.exit(1)
    for k in args.keys.split("+"):
        if k not in VK:
            sys.exit(f"Unknown key name {k!r}. Use: {', '.join(VK)}")

    if not single_instance():
        log.error("Dictation relay is already running (check the tray). Not starting a second copy.")
        sys.exit(0)

    icon = pystray.Icon("dictation-relay", make_icon(ICONS["offline"][0]), ICONS["offline"][1])
    relay = Relay(args, icon)

    def quit_(icon_, _item):
        relay.shutdown()
        icon_.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem(f"Hotkey: {args.keys}", None, enabled=False),
        pystray.MenuItem("Open log", lambda *_: os.startfile(HERE / "relay.log")),
        pystray.MenuItem("Quit", quit_),
    )
    threading.Thread(target=relay.run_loop, daemon=True).start()
    log.info("Dictation relay started. Hotkey: %s", args.keys)
    icon.run()


if __name__ == "__main__":
    main()
