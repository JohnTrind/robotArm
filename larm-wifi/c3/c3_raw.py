"""Robust raw-REPL driver for the CyberBrick C3 whose firmware auto-runs
/app/rc_main.py on boot and won't yield a REPL to mpremote.

We break in by spamming Ctrl-C over pyserial, enter raw REPL, and exec code.

Usage:
    python c3_raw.py COM18 runfile <local_script.py>   # run a script, print output
    python c3_raw.py COM18 eval "<python expr/stmts>"   # run inline code
"""
import sys
import time
import serial


def _read_until(s, pattern, timeout=5.0):
    buf = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        chunk = s.read(s.in_waiting or 1)
        if chunk:
            buf += chunk
            if pattern in buf:
                return buf
        else:
            time.sleep(0.01)
    return buf


def break_in(s):
    # Spam Ctrl-C to interrupt the running asyncio loop in rc_main.
    s.write(b"\r\x03\x03")
    t0 = time.time()
    while time.time() - t0 < 3.0:
        s.write(b"\x03")
        s.read(s.in_waiting or 1)
        time.sleep(0.04)
    s.reset_input_buffer()


def enter_raw(s):
    s.write(b"\r\x01")
    _read_until(s, b"raw REPL", timeout=3.0)
    _read_until(s, b">", timeout=2.0)


def exec_raw(s, code):
    if isinstance(code, str):
        code = code.encode()
    s.reset_input_buffer()
    s.write(code)
    s.write(b"\x04")
    _read_until(s, b"OK", timeout=5.0)
    out = _read_until(s, b"\x04", timeout=30.0)
    out = out.split(b"\x04")[0]
    err = _read_until(s, b"\x04", timeout=10.0)
    err = err.split(b"\x04")[0]
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def connect(port):
    s = serial.Serial(port, 115200, timeout=0.1)
    time.sleep(0.2)
    break_in(s)
    enter_raw(s)
    return s


def main():
    port = sys.argv[1]
    mode = sys.argv[2]
    if mode == "runfile":
        with open(sys.argv[3], "r", encoding="utf-8") as f:
            code = f.read()
    elif mode == "eval":
        code = sys.argv[3]
    else:
        print("unknown mode", mode)
        return
    s = connect(port)
    try:
        out, err = exec_raw(s, code)
        sys.stdout.write(out)
        if err.strip():
            sys.stderr.write("\n--- device stderr ---\n" + err)
    finally:
        s.write(b"\x02")  # back to friendly REPL
        s.close()


if __name__ == "__main__":
    main()
