"""Sonda WS: mede RTT e confirma que os comandos chegam (contadores rx/sent),
SEM mexer servos (envia idx1 -> 92 = neutro do contínuo).
Uso: python _ws_probe.py <ip>
"""
import socket
import base64
import os
import json
import time
import sys
import urllib.request


def cfg(ip):
    return json.loads(urllib.request.urlopen(
        "http://%s/app/larm/config" % ip, timeout=5).read())


def frame(t):
    b = t.encode()
    n = len(b)
    m = os.urandom(4)
    return bytes([0x81, 0x80 | n]) + m + bytes(b[i] ^ m[i % 4] for i in range(n))


def read_frame(s):
    h = s.recv(2)
    ln = h[1] & 0x7F
    return s.recv(ln) if ln else b""


ip = sys.argv[1]
c0 = cfg(ip)
print("antes : rx=%s sent=%s ws=%s" % (c0.get("rx"), c0.get("sent"), c0.get("ws")))
port = c0.get("ws", 81)

s = socket.create_connection((ip, port), timeout=5)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET / HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n" % (ip, key)).encode())
s.recv(1024)  # 101

rtts = []
for i in range(8):
    t = time.time()
    s.sendall(frame("1,92"))      # neutro -> nao mexe
    read_frame(s)                 # ack "k"
    rtts.append((time.time() - t) * 1000)
    time.sleep(0.05)
s.close()

rtts.sort()
print("RTT WS ms: min=%.0f med=%.0f max=%.0f" %
      (rtts[0], rtts[len(rtts) // 2], rtts[-1]))
time.sleep(0.3)
c1 = cfg(ip)
print("depois: rx=%s sent=%s last=%s" %
      (c1.get("rx"), c1.get("sent"), c1.get("last")))
print("delta : rx=+%d sent=+%d  (esperado +8 cada)" %
      (c1.get("rx", 0) - c0.get("rx", 0), c1.get("sent", 0) - c0.get("sent", 0)))
