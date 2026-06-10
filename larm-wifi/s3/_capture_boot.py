import sys, time, serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM17"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 18.0

s = serial.Serial(port, 115200, timeout=0.2)
time.sleep(0.3)
# interrupt anything running, then soft-reset to run boot.py + main.py fresh
s.write(b"\x03")          # Ctrl-C
time.sleep(0.3)
s.reset_input_buffer()
s.write(b"\x04")          # Ctrl-D soft reset
t0 = time.time()
buf = b""
while time.time() - t0 < secs:
    data = s.read(512)
    if data:
        buf += data
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()
s.close()
