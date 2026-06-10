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

Setup (camara FIXA a ver o braco de fora):
  marcador ID 0 -> colado na GARRA do braco
  marcador ID 1 -> colado no OBJETO alvo
  a base roda ate a garra ficar alinhada (horizontalmente) com o objeto.

Uso:
  python vision.py                       # so preview + overlay (nao mexe o braco)
  python vision.py --control             # fecha o loop: alinha garra->alvo
  python vision.py --control --invert    # se a base rodar p/ o lado errado
  python vision.py --src 0               # webcam local em vez do Pixel
  python vision.py --list                # imprime IDs ArUco vistos e sai

Marcadores: dicionario 4x4_50 (gera com make_markers_pdf.py).
"""
import argparse
import json
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

# --- modo APONTAR (camara top-down): roda a base p/ a garra apontar ao alvo ---
PIVOT_FILE = "larm_pivot.json"   # centro de rotacao da base (auto-detetado)
# histerese: assenta com folga (ENTER) e so volta a corrigir se passar REENGAGE.
# Evita o "ficar a pensar" (cacar a alvo) quando o angulo e ruidoso.
POINT_DEAD_DEG = 12    # |erro| < isto -> apontado, para
POINT_REENGAGE = 24    # so volta a mexer se |erro| passar disto
POINT_KP = 3           # % de motor por grau de erro
POINT_MIN, POINT_MAX = 35, 70    # limites do nudge da base

# --- modo ALCANCAR (--reach): visual servoing por JACOBIANO de imagem.
# Sonda cada atuador (base, junta1, junta2), mede o vetor que faz a garra na
# imagem, e combina-os p/ levar a garra ao objeto. Funciona em QUALQUER angulo
# de camara — nao assume o que cada atuador faz. A camara fecha o loop.
REACH_OVERLAP = 26     # garra<->objeto < isto px -> sobreposto, para
REACH_REENGAGE = 46    # histerese: so volta a mexer se a distancia passar disto
JAC_PROBE_MS = 300     # duracao do toque de sondagem de cada atuador
JAC_GAIN = 0.55        # fracao do erro a perseguir por passo (baixo = suave)
JAC_MAX_MS = 480       # tecto da duracao de cada nudge de correcao
JAC_REPROBE = 5        # re-sonda o jacobiano a cada N passos (a pose muda)


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


def s3_joint(idx, dirn, ms=400, ampl=35, neutral=92):
    """Nudge time-boxed a uma junta CONTINUA. dirn -1/+1 -> velocidade (o
    'angulo' e velocidade nos servos continuos). Auto-para ao fim de ms."""
    angle = neutral + int(dirn) * ampl
    url = "%s/app/larm/move?idx=%d&angle=%d&ms=%d" % (_S3, idx, angle, ms)
    try:
        urllib.request.urlopen(url, timeout=1.5).read()
    except Exception as e:
        print("[s3] junta falhou:", e)


def s3_stopall():
    try:
        urllib.request.urlopen("%s/app/larm/stopall" % _S3, timeout=1.5).read()
    except Exception:
        pass


# --- helpers do modo APONTAR ---
def _settle(kind, handle, detector, ids_want, n=5, flush=8):
    """Le frames frescos e devolve {id: centro medio} dos ids pedidos."""
    for _ in range(flush):
        grab(kind, handle)
    acc = {i: [] for i in ids_want}
    for _ in range(n):
        f = grab(kind, handle)
        corners, ids, _ = detector.detectMarkers(f)
        if ids is not None:
            for c, i in zip(corners, ids.flatten()):
                if int(i) in acc:
                    acc[int(i)].append(c.reshape(4, 2).mean(axis=0))
    return {i: (np.mean(v, axis=0) if v else None) for i, v in acc.items()}


def _ang(p, pivot):
    import math
    return math.degrees(math.atan2(p[1] - pivot[1], p[0] - pivot[0]))


def _wrap(a):
    return (a + 180) % 360 - 180


def load_pivot():
    try:
        with open(PIVOT_FILE) as f:
            d = json.load(f)
        return d["cx"], d["cy"], d.get("dir", 1), d.get("r", 0)
    except Exception:
        return None


def calibrate_pivot(kind, handle, detector, grip_id):
    """Roda a base em passos, traca o arco da garra, ajusta o circulo ->
    pivot. Tambem deteta o sentido (base+ aumenta o angulo?). Guarda em json."""
    print("[point] a calibrar o pivot: rodo a base e vejo a garra dar a volta...")
    pts = []
    m = _settle(kind, handle, detector, [grip_id])
    if m[grip_id] is not None:
        pts.append(m[grip_id])
    for _ in range(6):
        s3_motor(60, 500)
        time.sleep(1.1)
        m = _settle(kind, handle, detector, [grip_id])
        if m[grip_id] is not None:
            pts.append(m[grip_id])
    s3_motor(0)
    if len(pts) < 4:
        print("[point] nao vi a garra o suficiente p/ calibrar."); return None
    pts = np.array(pts)
    x, y = pts[:, 0], pts[:, 1]
    A = np.c_[2 * x, 2 * y, np.ones(len(pts))]
    cx, cy, c = np.linalg.lstsq(A, x ** 2 + y ** 2, rcond=None)[0]
    r = float(np.sqrt(c + cx ** 2 + cy ** 2))
    # sentido: base+ fez o angulo subir ou descer?
    import math
    angs = np.unwrap([math.radians(_ang(p, (cx, cy))) for p in pts])
    bdir = 1 if angs[-1] >= angs[0] else -1
    out = {"cx": float(cx), "cy": float(cy), "dir": int(bdir), "r": r}
    try:
        with open(PIVOT_FILE, "w") as f:
            json.dump(out, f)
    except Exception as e:
        print("[point] nao guardei pivot:", e)
    print("[point] pivot (%.0f,%.0f) raio=%.0fpx sentido=%+d -> %s" %
          (cx, cy, r, bdir, PIVOT_FILE))
    if r < 40:
        print("[point] AVISO: raio pequeno (%.0fpx) -> angulo ruidoso. P/ mais "
              "precisao, cola o marcador da garra MAIS PARA FORA no braco." % r)
    return cx, cy, bdir, r


def point_loop(kind, handle, detector, grip_id, tgt_id, pivot):
    """Roda a base ate a garra apontar para o alvo (ID alvo). Continuo: se
    moveres o objeto, o braco re-aponta. Ctrl-C ou alvo perdido -> para."""
    cx, cy, bdir, r = pivot
    print("[point] APONTAR garra(%d)->alvo(%d). pivot(%.0f,%.0f). Ctrl-C p/ sair."
          % (grip_id, tgt_id, cx, cy))
    holding = False
    try:
        while True:
            m = _settle(kind, handle, detector, [grip_id, tgt_id])
            g, t = m[grip_id], m[tgt_id]
            if g is None or t is None:
                if not holding:
                    s3_motor(0); holding = True
                print("[point] falta %s%s" % (
                    "garra " if g is None else "", "alvo" if t is None else ""))
                continue
            err = _wrap(_ang(t, (cx, cy)) - _ang(g, (cx, cy)))
            # histerese: se ja esta seguro, so re-engata com erro grande;
            # senao, considera apontado dentro do deadband.
            limiar = POINT_REENGAGE if holding else POINT_DEAD_DEG
            if abs(err) < limiar:
                if not holding:
                    s3_motor(0); holding = True
                    print("[point] APONTADO (erro %+.0f deg) — seguro." % err)
                continue
            holding = False
            mag = max(POINT_MIN, min(POINT_MAX, abs(err) * POINT_KP))
            pct = int(bdir * (mag if err > 0 else -mag))
            dur = int(max(250, min(700, abs(err) * 6)))
            print("[point] erro=%+.0f deg -> base %+d (%dms)" % (err, pct, dur))
            s3_motor(pct, dur)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        s3_stopall()
        print("[point] base parada.")


def _grab2(kind, handle, detector, grip_id, tgt_id):
    m = _settle(kind, handle, detector, [grip_id, tgt_id])
    return m[grip_id], m[tgt_id]


def _act(name, u_ms):
    """Atua 1 atuador por |u_ms| ms no sentido sign(u_ms). |u_ms|<60 -> ignora.
    name: 'base' (motor), 'j1'/'j2' (juntas continuas)."""
    ms = int(min(JAC_MAX_MS, abs(u_ms)))
    if ms < 60:
        return
    d = 1 if u_ms > 0 else -1
    if name == "base":
        s3_motor(55 * d, ms)
    elif name == "j1":
        s3_joint(1, d, ms)
    elif name == "j2":
        s3_joint(2, d, ms)


def probe_jacobian(kind, handle, detector, grip_id):
    """Toca cada atuador e mede o vetor que a garra faz na imagem -> colunas do
    jacobiano (2x3, px por JAC_PROBE_MS). Toca + e desfaz; se perder a garra
    nesse sentido, tenta o oposto. Independente do angulo da camara."""
    acts = ["base", "j1", "j2"]
    cols = []
    print("[reach] a sondar o jacobiano (toco cada atuador, vejo a garra)...")
    for a in acts:
        col = (0.0, 0.0)
        for sgn in (1, -1):
            m = _settle(kind, handle, detector, [grip_id])
            g0 = m[grip_id]
            if g0 is None:
                continue
            _act(a, sgn * JAC_PROBE_MS)
            time.sleep(0.9)
            m = _settle(kind, handle, detector, [grip_id])
            g1 = m[grip_id]
            _act(a, -sgn * JAC_PROBE_MS)        # desfaz o toque
            time.sleep(0.9)
            if g1 is not None:
                col = ((g1[0] - g0[0]) * sgn, (g1[1] - g0[1]) * sgn)
                break
        cols.append(col)
        print("[reach]  %-4s -> garra move (%+.0f,%+.0f) px" % (a, col[0], col[1]))
    return np.array(cols).T, acts        # J = 2x3


def reach_jacobian(kind, handle, detector, grip_id, tgt_id):
    """Visual servoing: sobrepoe a garra(grip_id) ao objeto(tgt_id) combinando
    base+juntas via o jacobiano de imagem. Para ao sobrepor; segue o objeto;
    recua o passo se a garra sair do enquadramento."""
    J, acts = probe_jacobian(kind, handle, detector, grip_id)
    print("[reach] SOBREPOR garra(%d)->objeto(%d) por jacobiano. Ctrl-C p/ sair."
          % (grip_id, tgt_id))
    holding = False
    lost = 0
    it = 0
    try:
        while True:
            it += 1
            if it % JAC_REPROBE == 0:        # a pose mudou -> re-sonda
                J, acts = probe_jacobian(kind, handle, detector, grip_id)
            g, t = _grab2(kind, handle, detector, grip_id, tgt_id)
            if g is None or t is None:
                if not holding:
                    s3_stopall(); holding = True
                print("[reach] perdi %s%s — parado" % (
                    "garra " if g is None else "",
                    "objeto" if t is None else ""))
                continue
            err = np.array([t[0] - g[0], t[1] - g[1]])
            d = float(np.hypot(*err))
            limiar = REACH_REENGAGE if holding else REACH_OVERLAP
            if d < limiar:
                if not holding:
                    s3_stopall(); holding = True
                    print("[reach] SOBREPOSTO (%.0fpx) — seguro." % d)
                continue
            holding = False
            u = np.linalg.pinv(J) @ (JAC_GAIN * err)    # unidades de JAC_PROBE_MS
            applied = []
            for name, ui in zip(acts, u):
                ms = float(ui) * JAC_PROBE_MS
                if abs(ms) >= 60:
                    _act(name, ms)
                    applied.append((name, ms))
                    time.sleep(0.7)
            print("[reach] dist=%.0fpx -> %s" % (d, ", ".join(
                "%s%+dms" % (n, int(m)) for n, m in applied) or "nada"))
            # se a garra saiu do enquadramento, RECUA o passo
            m = _settle(kind, handle, detector, [grip_id])
            if m[grip_id] is None:
                lost += 1
                print("[reach] garra fora do enquadramento -> recuo o passo")
                for name, ms in reversed(applied):
                    _act(name, -ms); time.sleep(0.6)
                if lost >= 3:
                    print("[reach] nao consigo manter no enquadramento — AFASTA "
                          "a camara p/ ver o espaco de trabalho todo, e re-corre.")
                    break
            else:
                lost = 0
    except KeyboardInterrupt:
        pass
    finally:
        s3_stopall()
        print("[reach] tudo parado.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=STREAM_URL,
                    help="URL MJPEG (default Pixel) ou indice de webcam (ex: 0)")
    ap.add_argument("--control", action="store_true",
                    help="FECHA o loop: roda a base p/ alinhar garra->alvo (mexe o braco)")
    ap.add_argument("--gripper-id", type=int, default=0,
                    help="ID ArUco colado na GARRA (default 0)")
    ap.add_argument("--target-id", type=int, default=1,
                    help="ID ArUco colado no OBJETO alvo (default 1)")
    ap.add_argument("--invert", action="store_true",
                    help="inverte o sentido da base (se rodar p/ o lado errado)")
    ap.add_argument("--point", action="store_true",
                    help="MODO APONTAR (camara top-down): roda a base p/ a garra "
                         "apontar ao alvo. Mexe o braco. Usa pivot guardado.")
    ap.add_argument("--calibrate-pivot", action="store_true",
                    help="(re)deteta o centro de rotacao da base e guarda-o")
    ap.add_argument("--reach", action="store_true",
                    help="MODO ALCANCAR (jacobiano): sonda os atuadores e leva a "
                         "garra a sobrepor o objeto. Qualquer angulo. Mexe o braco.")
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

    if args.reach:
        # ALCANCAR (jacobiano): nao precisa de pivot, sonda os atuadores.
        reach_jacobian(kind, handle, detector, args.gripper_id, args.target_id)
        if kind == "mjpeg":
            handle.close()
        else:
            handle.release()
        return

    if args.calibrate_pivot or args.point:
        pivot = None
        if not args.calibrate_pivot:
            lp = load_pivot()
            if lp:
                pivot = lp
                print("[point] pivot de %s: (%.0f,%.0f) raio=%.0f" %
                      (PIVOT_FILE, lp[0], lp[1], lp[3]))
        if pivot is None:
            pivot = calibrate_pivot(kind, handle, detector, args.gripper_id)
        if pivot is None:
            print("[point] sem pivot — aponta a camara de cima e mostra a garra.")
            return
        if args.point:
            point_loop(kind, handle, detector, args.gripper_id,
                       args.target_id, pivot)
        if kind == "mjpeg":
            handle.close()
        else:
            handle.release()
        return

    moving = False
    try:
        while True:
            frame = grab(kind, handle)
            h, w = frame.shape[:2]
            corners, ids, _ = detector.detectMarkers(frame)

            grip = tgt = None
            if ids is not None:
                cv2.aruco.drawDetectedMarkers(frame, corners, ids)
                for c, i in zip(corners, ids.flatten()):
                    ctr = c.reshape(4, 2).mean(axis=0)
                    if int(i) == args.gripper_id:
                        grip = ctr
                    elif int(i) == args.target_id:
                        tgt = ctr

            state, pct = "", 0
            if grip is None and tgt is None:
                state = "sem garra nem alvo"
            elif grip is None:
                state = "falta GARRA (id %d)" % args.gripper_id
            elif tgt is None:
                state = "falta ALVO (id %d)" % args.target_id
            else:
                # erro = posicao horizontal da garra menos a do alvo
                err = (grip[0] - tgt[0]) / w     # <0: garra a esq do alvo
                cv2.circle(frame, (int(grip[0]), int(grip[1])), 8, (60, 255, 60), -1)
                cv2.circle(frame, (int(tgt[0]), int(tgt[1])), 8, (0, 215, 255), -1)
                cv2.line(frame, (int(grip[0]), int(grip[1])),
                         (int(tgt[0]), int(tgt[1])), (200, 200, 0), 2)
                if abs(err) < DEADBAND:
                    state, pct = "ALINHADO", 0
                else:
                    mag = min(1.0, (abs(err) - DEADBAND) / (0.5 - DEADBAND))
                    speed = int(SPEED_MIN + mag * (SPEED_MAX - SPEED_MIN))
                    # garra a ESQ do alvo (err<0) -> precisa ir p/ a DIREITA.
                    # o sentido fisico da base e desconhecido -> --invert ajusta.
                    direction = 1 if err < 0 else -1
                    if args.invert:
                        direction = -direction
                    pct = direction * speed
                    state = "garra->alvo  err=%+.2f  base %s" % (
                        err, "->" if pct > 0 else "<-")
                cv2.putText(frame, "err=%+.2f" % err, (10, h - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 0), 2)

            # controlo (so com --control): re-nudge enquanto desalinhado; ao
            # alinhar/perder um marcador -> para a base uma vez.
            if args.control:
                if pct != 0:
                    s3_motor(pct)
                    moving = True
                elif moving:
                    s3_motor(0)
                    moving = False

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
