"""Smoke-test do WebSocket do larm SEM mexer servos.
Uso: python _ws_test.py <ip> [porta]
"""
import socket
import base64
import os
import hashlib
import json
import time
import sys
import urllib.request

ip = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 81

# 1. GET /config dispara o arranque do servidor WS e diz a porta/canal
cfg = json.loads(urllib.request.urlopen(
    "http://%s/app/larm/config" % ip, timeout=5).read())
print("config: ws=%s canal=%s servos=%d" %
      (cfg.get("ws"), cfg.get("channel"), len(cfg.get("servos", []))))
port = cfg.get("ws", port)

# 2. handshake WS (com retry enquanto o servidor liga o socket)
s = None
for _ in range(10):
    try:
        s = socket.create_connection((ip, port), timeout=5)
        break
    except OSError:
        time.sleep(0.3)
if s is None:
    print("FAIL: nao consegui ligar ao WS :%d" % port)
    sys.exit(1)

key = base64.b64encode(os.urandom(16)).decode()
req = ("GET / HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
       "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
       "Sec-WebSocket-Version: 13\r\n\r\n" % (ip, key))
s.sendall(req.encode())
resp = s.recv(1024).decode("latin1")
line0 = resp.splitlines()[0] if resp else "(vazio)"
acc = base64.b64encode(hashlib.sha1(
    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
print("handshake:", line0)
print("accept correto:", acc in resp)


def frame(t):
    b = t.encode()
    n = len(b)
    m = os.urandom(4)
    return bytes([0x81, 0x80 | n]) + m + bytes(b[i] ^ m[i % 4] for i in range(n))


# 3. comando no-op (idx 9 nao existe -> servidor ignora, nada mexe)
s.sendall(frame("9,90"))
time.sleep(0.2)
print("comando no-op enviado por WS: OK (nada mexeu)")
s.close()
print("WS OK")
