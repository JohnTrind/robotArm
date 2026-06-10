#!/usr/bin/env python3
"""pixel_cam.py -- liga a camara do Pixel (Waydroid) ao PC. Corre do PC.

A app "Android IP Camera" corre DENTRO do Waydroid e serve MJPEG em
https://192.168.240.112:4444/stream -- mas esse IP e a rede INTERNA do
Waydroid, so existe dentro do telemovel. Esta ponte resolve isso:

  PC  --adb forward-->  telemovel:4445 (relay python)  -->  192.168.240.112:4444

Depois o PC le https://localhost:4444/stream (o relay e raw TCP, o TLS passa).

Uso (com o Pixel ligado por USB, depurACAO USB ON):
  python pixel_cam.py            # arranca tudo e testa
  python pixel_cam.py --stop     # desliga o relay + forward

Pre-requisitos no telemovel: Waydroid a correr + a app IP Camera aberta com
username/password configurados (a app recusa servir sem credenciais validas).
Para reiniciar a sessao Waydroid: ver pixel_camera.md.
"""
import argparse
import subprocess
import ssl
import sys
import urllib.request

PHONE_INTERNAL = "192.168.240.112:4444"   # IP da app dentro do Waydroid
RELAY_PORT = 4445                         # porta do relay no telemovel
PC_PORT = 4444                            # porta vista no PC (localhost)
RELAY_REMOTE = "/home/phablet/relay.py"


def adb(*args, capture=True):
    return subprocess.run(["adb", *args], capture_output=capture, text=True)


def relay_src():
    host, port = PHONE_INTERNAL.split(":")
    return ('''import socket, threading
LISTEN=("127.0.0.1",%d); TARGET=("%s",%s)
def pipe(a,b):
    try:
        while True:
            d=a.recv(65536)
            if not d: break
            b.sendall(d)
    except Exception: pass
    finally:
        for s in (a,b):
            try: s.shutdown(socket.SHUT_RDWR)
            except Exception: pass
def handle(c):
    try: up=socket.create_connection(TARGET,timeout=10)
    except Exception: c.close(); return
    threading.Thread(target=pipe,args=(c,up),daemon=True).start()
    threading.Thread(target=pipe,args=(up,c),daemon=True).start()
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(LISTEN); s.listen(16)
print("relay up",LISTEN,"->",TARGET,flush=True)
while True:
    c,_=s.accept(); handle(c)
''' % (RELAY_PORT, host, port))


def stop():
    adb("shell", "pkill -f '[r]elay.py'")
    adb("forward", "--remove", "tcp:%d" % PC_PORT)
    print("[pixel_cam] relay + forward desligados.")


def start():
    # 1) instala o relay no telemovel
    p = subprocess.run(["adb", "shell", "cat > %s" % RELAY_REMOTE],
                       input=relay_src(), text=True)
    if p.returncode != 0:
        print("[pixel_cam] adb nao encontrou o telemovel?"); sys.exit(1)
    # 2) (re)arranca o relay destacado
    adb("shell", "pkill -f '[r]elay.py'")
    adb("shell", "(setsid python3 -u %s >/home/phablet/relay.log 2>&1 </dev/null &)"
        " ; echo started" % RELAY_REMOTE)
    log = adb("shell", "sleep 1; cat /home/phablet/relay.log").stdout.strip()
    print("[pixel_cam] relay:", log or "(sem log)")
    # 3) adb forward PC:4444 -> telemovel:4445
    adb("forward", "--remove", "tcp:%d" % PC_PORT)
    adb("forward", "tcp:%d" % PC_PORT, "tcp:%d" % RELAY_PORT)
    # 4) testa a cadeia
    url = "https://localhost:%d/stream" % PC_PORT
    try:
        ctx = ssl._create_unverified_context()
        r = urllib.request.urlopen(url, timeout=8, context=ctx)
        ct = r.headers.get("Content-Type", "")
        ok = "multipart" in ct
        print("[pixel_cam] %s -> HTTP %d (%s)" % (url, r.status, ct))
        print("[pixel_cam] OK! corre:  python vision.py" if ok else
              "[pixel_cam] responde mas nao e MJPEG -- a app esta a servir?")
    except urllib.error.HTTPError as e:
        body = e.read()[:120].decode("utf-8", "replace")
        print("[pixel_cam] HTTP %d: %s" % (e.code, body))
        print("[pixel_cam] -> configura username+password na app (ela recusa sem).")
    except Exception as e:
        print("[pixel_cam] cadeia falhou:", e)
        print("[pixel_cam] -> Waydroid a correr? app aberta? ver pixel_camera.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()
    stop() if args.stop else start()
