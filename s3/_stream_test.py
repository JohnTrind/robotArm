"""Testa o modelo de stream: envia 1 intencao 'v,1,-1' por WS e mede o 'sent'
ao longo do tempo (deve disparar ~30Hz e parar sozinho ~0.5s depois)."""
import socket, base64, os, json, time, sys, urllib.request

ip = sys.argv[1]


def sent():
    return json.loads(urllib.request.urlopen(
        "http://%s/app/larm/config" % ip, timeout=5).read()).get("sent")


def frame(t):
    b = t.encode(); n = len(b); m = os.urandom(4)
    return bytes([0x81, 0x80 | n]) + m + bytes(b[i] ^ m[i % 4] for i in range(n))


s = socket.create_connection((ip, 81), timeout=5)
k = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET / HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (ip, k)).encode())
s.recv(1024)
s.setblocking(False)

s0 = sent()
print("t=0.0  sent=%d  -> envio 1x 'v,1,-1' (sem heartbeat)" % s0)
s.send(frame("v,1,-1"))
for t in (0.3, 0.6, 1.2):
    time.sleep(t if t == 0.3 else 0.3)
    print("t=%.1f  sent=%d" % (t, sent()))
s.close()
print("esperado: sobe ~15 ate t=0.6 e ESTABILIZA depois (parou sozinho).")
