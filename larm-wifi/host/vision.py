#!/usr/bin/env python3
"""vision.py -- olhos do braco L-ONE (fase 2: autonomia por visual servoing).

O hardware NAO tem sensores de posicao (juntas continuas + motor DC). A camara
e o que FECHA o loop de posicao que falta: o PC olha para a imagem, ve onde
esta o alvo, e roda a base ate o alvo ficar centrado. E "visual servoing".

Pipeline:
  MJPEG (camara do Pixel via Waydroid + relay adb) -> OpenCV
  -> deteta marcadores ArUco (imprime uns quadrados de papel)
  -> erro horizontal dx do ALVO em relacao ao centro
  -> controlador P -> /app/larm/motor (base) no S3, time-boxed (auto-para)

SEGURANCA: o controlo do braco esta DESLIGADO por defeito. Sem --control isto
so MOSTRA a imagem + overlay (nao mexe nada). Cada comando ao S3 leva &ms (o
braco para sozinho se o loop de visao parar) e ao sair manda /stopall.

Uso:
  python vision.py                       # so preview + overlay (nao mexe o braco)
  python vision.py --control             # fecha o loop: roda a base p/ centrar
  python vision.py --src 0               # webcam local em vez do Pixel
  python vision.py --list                # imprime IDs ArUco vistos e sai

Marcadores: dicionario 4x4_50. Cola o ID do ALVO (default 0) no objeto.
Gera/imprime marcadores em https://chev.me/arucogen (4x4, 50).
"""
import argparse
import ssl
import sys
import time
import urllib.request

import cv2
import numpy as np

# ---- defaults (afina por flag) ----
STREAM_URL = "https://localhost:4444/stream"   # Pixel via relay adb (porta 4444)
S3_BASE = "http://192.168.1.79"                # IP do S3 na LAN
BASE_MOTOR_IDX = 1                             # MOTOR1 = base/yaw
ARUCO_DICT = cv2.aruco.DICT_4X4_50

_S3 = S3_BASE                                  # runtime (sobreposto por --s3)

# controlador (base): tudo em fracao da largura da imagem
DEADBAND = 0.06        # |dx| < isto -> considera centrado, para
SPEED_MIN, SPEED_MAX = 25, 55   # % de velocidade do motor (P-scaled)
CMD_MS = 220           # cada nudge dura isto e auto-para (failsafe do S3)


class MjpegStream:
    """Le multipart/x-mixed-replace (boundary=frame) com SSL nao-verificado
    (certificado self-signed da app). Devolve frames BGR via cv2.imdecode."""

    def __init__(self, url, timeout=10):
        self.url = url
        ctx = ssl._create_unverified_context() if url.startswith("https") else None
        req = urllib.request.Request(url, headers={"User-Agent": "larm-vision"})
        self.r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        self.buf = b""

    def read(self):
        # acumula ate ter um JPEG completo (SOI ffd8 ... EOI ffd9)
        while True:
            a = self.buf.find(b"\xff\xd8")
            b = self.buf.find(b"\xff\xd9", a + 2) if a != -1 else -1
            if a != -1 and b != -1:
                jpg = self.buf[a:b + 2]
                self.buf = self.buf[b + 2:]
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    return img
            chunk = self.r.read(8192)
            if not chunk:
                raise OSError("stream terminou")
            self.buf += chunk
            if len(self.buf) > 4_000_000:        # guarda contra lixo
                self.buf = self.buf[-1_000_000:]

    def close(self):
        try:
            self.r.close()
        except Exception:
            pass


def open_source(src):
    """src = URL MJPEG, ou indice de webcam ('0'), ou caminho de ficheiro."""
    if isinstance(src, str) and src.startswith("http"):
        return ("mjpeg", MjpegStream(src))
    cap = cv2.VideoCapture(int(src) if str(src).isdigit() else src)
    if not cap.isOpened():
        raise OSError("nao abri a fonte de video: %s" % src)
    return ("cv", cap)


def grab(kind, handle):
    if kind == "mjpeg":
        return handle.read()
    ok, frame = handle.read()
    if not ok:
        raise OSError("fim do video")
    return frame


def s3_motor(pct, ms=CMD_MS):
    """Nudge time-boxed ao motor da base. pct=0 para imediatamente."""
    url = "%s/app/larm/motor?idx=%d&pct=%d" % (_S3, BASE_MOTOR_IDX, int(pct))
    if pct != 0:
        url += "&ms=%d" % ms
    try:
        urllib.request.urlopen(url, timeout=1.5).read()
    except Exception as e:
        print("[s3] falhou:", e)


def s3_stopall():
    try:
        urllib.request.urlopen("%s/app/larm/stopall" % _S3, timeout=1.5).read()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=STREAM_URL,
                    help="URL MJPEG (default Pixel) ou indice de webcam (ex: 0)")
    ap.add_argument("--control", action="store_true",
                    help="FECHA o loop: roda a base p/ centrar o alvo (mexe o braco)")
    ap.add_argument("--target-id", type=int, default=0, help="ID ArUco do alvo")
    ap.add_argument("--s3", default=S3_BASE, help="base URL do S3")
    ap.add_argument("--list", action="store_true",
                    help="imprime IDs ArUco vistos durante ~3s e sai")
    args = ap.parse_args()

    global _S3
    _S3 = args.s3

    kind, handle = open_source(args.src)
    print("[vision] fonte:", args.src, "| controlo:",
          "ON" if args.control else "OFF (so preview)")

    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())

    if args.list:
        seen = {}
        t0 = time.time()
        while time.time() - t0 < 3:
            frame = grab(kind, handle)
            corners, ids, _ = detector.detectMarkers(frame)
            if ids is not None:
                for i in ids.flatten():
                    seen[int(i)] = seen.get(int(i), 0) + 1
        print("[vision] IDs vistos (id: frames):", seen or "nenhum")
        return

    last_state = None
    try:
        while True:
            frame = grab(kind, handle)
            h, w = frame.shape[:2]
            cx_frame = w / 2.0
            corners, ids, _ = detector.detectMarkers(frame)

            target = None
            if ids is not None:
                cv2.aruco.drawDetectedMarkers(frame, corners, ids)
                for c, i in zip(corners, ids.flatten()):
                    if int(i) == args.target_id:
                        target = c.reshape(4, 2).mean(axis=0)

            # linha do centro
            cv2.line(frame, (int(cx_frame), 0), (int(cx_frame), h), (90, 90, 90), 1)

            state, pct = "sem alvo", 0
            if target is not None:
                tx, ty = target
                dx = (tx - cx_frame) / w        # -0.5..0.5 (fracao da largura)
                cv2.circle(frame, (int(tx), int(ty)), 8, (0, 215, 255), -1)
                cv2.line(frame, (int(cx_frame), int(ty)), (int(tx), int(ty)),
                         (0, 215, 255), 2)
                if abs(dx) < DEADBAND:
                    state, pct = "CENTRADO", 0
                else:
                    mag = min(1.0, (abs(dx) - DEADBAND) / (0.5 - DEADBAND))
                    speed = int(SPEED_MIN + mag * (SPEED_MAX - SPEED_MIN))
                    pct = speed if dx > 0 else -speed
                    state = "alvo a %s -> base %s" % (
                        "direita" if dx > 0 else "esquerda",
                        "->" if pct > 0 else "<-")
                cv2.putText(frame, "dx=%+.2f" % dx, (10, h - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 215, 255), 2)

            # controlo (so com --control); so manda quando o estado muda OU
            # ha movimento (re-nudge continuo enquanto nao centrado)
            if args.control:
                if pct != 0:
                    s3_motor(pct)
                elif last_state not in (None, "CENTRADO", "sem alvo"):
                    s3_motor(0)            # acabou de centrar/perder -> para
                last_state = state

            tag = ("CTRL " if args.control else "VER  ") + state
            cv2.putText(frame, tag, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (60, 255, 60) if args.control else (220, 220, 220), 2)
            cv2.imshow("larm vision  (q=sair)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        if args.control:
            s3_stopall()
        if kind == "mjpeg":
            handle.close()
        else:
            handle.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
