import sys, time, serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM18"
s = serial.Serial(port, 115200, timeout=0.1)

# Spam Ctrl-C for a few seconds to try to break out of rc_main into the REPL.
t0 = time.time()
buf = b""
while time.time() - t0 < 4.0:
    s.write(b"\x03")
    data = s.read(256)
    if data:
        buf += data
    time.sleep(0.05)

# Ask for a prompt + a harmless command.
s.write(b"\r\n")
time.sleep(0.2)
s.reset_input_buffer()
s.write(b"import os; print('FS>>>', os.listdir('/'))\r\n")
time.sleep(0.6)
out = s.read(2048)
s.close()

print("=== tail of boot/ctrlC stream ===")
print(buf[-400:].decode("utf-8", "replace"))
print("=== response to command ===")
print(out.decode("utf-8", "replace"))
