# Linux Keylogger

A passive Linux keyboard logger that reads `EV_KEY` events directly from
`/dev/input/event*` using [`python-evdev`](https://python-evdev.readthedocs.io/).
It decodes US-layout keystrokes and writes either human-readable text or raw
JSONL events.

The logger does not grab the keyboard, inject input, persist itself, or send
data over the network. It only reads input devices and appends to a local file.

## Features

- Direct `evdev` capture from `/dev/input/event*`.
- Automatic keyboard detection.
- Multiple keyboards supported simultaneously.
- Hotplug rescan every 5 seconds by default.
- US keyboard layout decoding.
- Shift, Ctrl, Alt, Meta, and CapsLock handling.
- Readable text mode with special-key markers.
- Raw JSONL mode containing key codes, names, values, and decoded text.
- Optional timestamps.
- Output file created with `0600` permissions.
- Read-only device access; normal keyboard input is unaffected.

## Requirements

- Linux
- Python 3.9+
- `python3-evdev`
- Root privileges, or read access to `/dev/input/event*`

On Debian/Kali:

```bash
sudo apt install python3-evdev
```

## Repository layout

```text
.
├── README.md
└── keylogger.py
```

## Installation

Clone or copy the project directory, then make the script executable:

```bash
cd keylogger
chmod +x keylogger.py
```

## Permissions

Input devices are normally owned by `root:input` with mode `0660`. The simplest
way to run the logger is with `sudo`:

```bash
sudo python3 keylogger.py --list
```

Alternatively, grant the account read access to the relevant input devices.
Do not make `/dev/input/event*` world-readable.

## Usage

List detected keyboard devices:

```bash
sudo python3 keylogger.py --list
```

Log decoded text to the default file (`keylog.txt`):

```bash
sudo python3 keylogger.py
```

Log to a specific file:

```bash
sudo python3 keylogger.py -o /tmp/keys.log
```

Log with timestamps:

```bash
sudo python3 keylogger.py --timestamp -o /tmp/keys.log
```

Log raw events as JSONL:

```bash
sudo python3 keylogger.py --raw -o /tmp/keys.jsonl
```

Capture only one device:

```bash
sudo python3 keylogger.py -d /dev/input/event3 -o /tmp/keys.log
```

Write to stdout instead of a file:

```bash
sudo python3 keylogger.py -o -
```

Disable device rescans:

```bash
sudo python3 keylogger.py --scan-interval 0 -o /tmp/keys.log
```

Stop the logger with `Ctrl+C`.

### Command-line options

| Option | Description |
|---|---|
| `-o, --output PATH` | Output file, or `-` for stdout. Default: `keylog.txt`. |
| `-d, --device PATH` | Event device to read. Repeatable. Default: auto-detect. |
| `--raw` | Write one JSON object per `EV_KEY` event. |
| `--timestamp` | Prefix decoded text with local timestamps. |
| `--list` | List detected keyboard devices and exit. |
| `--scan-interval SEC` | Device rescan interval. `0` disables. Default: `5`. |
| `-h, --help` | Show help. |

## Output formats

### Text mode

Enter produces a newline, Tab produces a tab, and non-text keys are written as
markers:

```text
Demo phrase: Hello, world! 123
[BACKSPACE][UP]
```

Examples of markers and combinations:

```text
[ESC]
[BACKSPACE]
[UP]
[CTRL+C]
[ALT+TAB]
[CTRL+ALT+T]
[SHIFT+TAB]
[CAPSLOCK]
```

### Raw JSONL mode

Each line is one `EV_KEY` event:

```json
{"time":"2026-11-04T10:15:00.123456+00:00","epoch":1762251300.123456,"device":"/dev/input/event3","name":"AT Translated Set 2 keyboard","code":30,"key":"KEY_A","value":1,"text":"a"}
```

`value` is the kernel event value:

- `0` — key release
- `1` — key press
- `2` — key repeat

## How it works

1. Scans `/dev/input/event*` and opens devices read-only.
2. Filters devices by checking for keyboard capabilities such as `KEY_A`,
   `KEY_Z`, `KEY_SPACE`, `KEY_ENTER`, and `KEY_LEFTSHIFT`.
3. Waits for readable devices with `select`.
4. Decodes `EV_KEY` events into characters or special-key markers.
5. Tracks Shift, Ctrl, Alt, Meta, and CapsLock state across events.
6. Appends output to the log and flushes after each key.
7. Rescans for newly attached or removed keyboards every 5 seconds.

The output file is created with mode `0600`. The logger never grabs the input
device, so it does not block or consume normal keyboard input.

## Safe demo / testing

The lowest-risk way to test is a disposable VM snapshot or a dedicated test
account. The logger captures every key from the selected devices, not just keys
sent to the focused window. During a demo, type only known, non-sensitive text.

### 1. Identify the keyboard device

```bash
sudo python3 keylogger.py --list
```

### 2. Prepare a private demo directory

```bash
sudo mkdir -p /tmp/keylogger-demo
sudo chown "$USER" /tmp/keylogger-demo
chmod 700 /tmp/keylogger-demo
```

### 3. Terminal A — run the logger for 30 seconds

```bash
sudo timeout --signal=INT 30s python3 keylogger.py \
  -o /tmp/keylogger-demo/keys.log
```

To limit capture to one keyboard:

```bash
sudo timeout --signal=INT 30s python3 keylogger.py \
  -d /dev/input/eventX \
  -o /tmp/keylogger-demo/keys.log
```

### 4. Terminal B — type the demo text

Open a scratch editor:

```bash
nano /tmp/keylogger-demo/demo.txt
```

Type a known phrase such as:

```text
Demo phrase: Hello, world! 123
```

Press Enter, Backspace, and an arrow key. Wait for the 30-second timeout to
stop the logger.

### 5. Inspect the log

```bash
sudo cat /tmp/keylogger-demo/keys.log
```

Expected output:

```text
Demo phrase: Hello, world! 123
[BACKSPACE][UP]
```

For raw events:

```bash
sudo timeout --signal=INT 30s python3 keylogger.py \
  --raw -o /tmp/keylogger-demo/keys.jsonl

sudo cat /tmp/keylogger-demo/keys.jsonl
```

### 6. Clean up

```bash
sudo rm -f /tmp/keylogger-demo/keys.log /tmp/keylogger-demo/keys.jsonl
rm -f /tmp/keylogger-demo/demo.txt
rmdir /tmp/keylogger-demo
```

If you used a VM snapshot, revert it instead.

### Demo safety checklist

- Use a VM or a dedicated test account.
- Start the logger after sudo authentication; do not type passwords while it is
  running.
- Restrict capture with `-d` when possible.
- Type only the demo phrase.
- Stop the logger as soon as the demo ends.
- Delete the log or revert the VM snapshot.
- Do not commit logs to the repository.

## Limitations

- The built-in keymap is US layout only. Other layouts require extending
  `CHAR_MAP` in `keylogger.py`.
- Keypad keys are decoded as their numeric/operator faces because NumLock state
  is not read.
- The logger records key events, not application context or window titles.
- The log is plaintext.
- The tool is Linux-only and relies on `/dev/input/event*`.
- No persistence, stealth, or network exfiltration is implemented.

## Validation

Basic syntax check:

```bash
python3 -m py_compile keylogger.py
```

The decoder and event loop were exercised with synthetic `EV_KEY` events.
Live device capture requires read access to `/dev/input/event*`, normally via
root.

## Scope

Use this project only on systems you own or have explicit authorization to
monitor.
