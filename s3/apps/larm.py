"""L-ONE arm remote -- o S3 faz de comando do braco CyberBrick.

MODELO UNICO DE CONTROLO (tudo passa por setpoints + stream):
  - o browser manda INTENCAO por WebSocket (porta 81): edge premir/largar +
    heartbeat 300ms. Cada intencao poe um SETPOINT com EXPIRY (500ms).
  - _stream() corre a ~30Hz e envia por ESP-NOW (unicast p/ o C3) o setpoint
    de cada atuador ATIVO. Expirou -> volta a neutro -> tail 250ms -> silencio.
  - HTTP (Postman/AI) e a sequencia usam o MESMO mecanismo: setpoint + expiry
    (time-boxed, default 1500ms, cap 3000ms) -> nada fica a rodar para sempre.
  - O C3 tem watchdog proprio (FAILSAFE_MS): silencio radio com algo a rodar
    -> para sozinho. Dupla protecao.

Atuadores (L-ONE 2.0): idx1/idx2 = servos rotacao CONTINUA (juntas; "angulo" =
velocidade, ~92 = parado -- largar o botao TEM de voltar ao neutro, senao roda
para sempre), idx3 = servo posicional (garra), MOTOR1 = motor DC (base/yaw).
Sem sensores -> sem feedback de posicao.

DEAD RECKONING (v2): o _stream integra a velocidade COMANDADA -> posicao
ESTIMADA por eixo (unidades arbitrarias "u"; juntas = (sp-home)*s, base =
pct*s). Calibracao humana: levar o braco a mao a uma referencia e ZERO.
Poses = snapshot da estimativa (+ angulo real da garra); "ir para pose" e um
loop fechado sobre a ESTIMATIVA (converge por construcao). A estimativa DERIVA
(carga, atrito, bateria, modelo linear) -> re-zerar quando preciso; reboot do
S3 perde a estimativa (refazer ZERO).
"""
from core.app import App
from core import display

import asyncio
import time
import network
import espnow
import hashlib
import binascii

try:
    import json
except ImportError:
    json = None

MAGIC = b"LA"
BCAST = b"\xff\xff\xff\xff\xff\xff"
# MAC do C3 (lido por USB 2026-06-10) -> UNICAST com retries MAC-layer.
# Pôr None para voltar a broadcast (ex.: C3 substituído).
C3_MAC = b"\x80\xf1\xb2\x1e\x13\x6c"
WS_PORT = 81
_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HARD_MIN, HARD_MAX = 30, 150
HTTP_MS_DEFAULT = 1500   # nudge HTTP sem ms= : roda isto e para (failsafe)
HTTP_MS_CAP = 3000

# Dead reckoning / goto (todas as defesas existentes continuam por baixo:
# expiry renovado a cada tick do goto + watchdog do C3).
# GOTO_SPD = 100: confirmado no hardware que ombro/cotovelo NAO tem forca para
# levantar em lento/medio -- so a 100% vencem a gravidade. A base fica mais
# devagar (fine tune da rotacao funciona bem em lento).
GOTO_SPD = 100       # % de deflexao das juntas durante o goto
GOTO_PCT = 40        # % do motor base durante o goto
GOTO_PCT_NEAR = 28   # % da base perto do alvo (lento = fine tune, confirmado)
GOTO_NEAR_U = 10.0   # u: distancia a que a base abranda
GOTO_BACK_U = 8.0    # u: recuo ALEM do alvo p/ aproximacao final sempre em +
GOTO_EPS_J = 4.0     # u: chegou (junta)
GOTO_EPS_M = 4.0     # u: chegou (base)
GOTO_TICK_MS = 60
GOTO_CAP_MS = 25000  # teto absoluto de um goto
POSES_MAX = 16
ANIMS_MAX = 12
ANIM_STEPS_MAX = 20

# (idx, nome, tipo, min, max, home/neutro).
# tipo: "cont" = rotacao continua (botoes = velocidade; largar = neutro = parar)
#       "pos"  = posicional (garra: slider + abrir/fechar; segura a posicao)
SERVOS = [
    (1, "Cotovelo", "cont", 30, 150, 92),
    (2, "Ombro",    "cont", 30, 150, 92),
    (3, "Garra",    "pos",  30, 150, 90),
]
# (idx, nome). Velocidade vem da UI (seletor lento/medio/rapido).
MOTORS = [(1, "Base")]


def _clampi(v, lo, hi):
    v = int(v)
    return lo if v < lo else hi if v > hi else v


async def _rx(reader, n):
    buf = b""
    while len(buf) < n:
        c = await reader.read(n - len(buf))
        if not c:
            raise OSError("eof")
        buf += c
    return buf


def _ws_text_frame(s):
    b = s.encode()
    n = len(b)
    if n < 126:
        return bytes([0x81, n]) + b
    return bytes([0x81, 126]) + n.to_bytes(2, "big") + b


async def _ws_frame(reader):
    h = await _rx(reader, 2)
    op = h[0] & 0x0F
    ln = h[1] & 0x7F
    masked = h[1] & 0x80
    if ln == 126:
        ln = int.from_bytes(await _rx(reader, 2), "big")
    elif ln == 127:
        ln = int.from_bytes(await _rx(reader, 8), "big")
    mask = await _rx(reader, 4) if masked else b"\0\0\0\0"
    data = bytearray(await _rx(reader, ln))
    if masked:
        for i in range(ln):
            data[i] ^= mask[i & 3]
    return op, bytes(data)


class LArm(App):
    name = "larm"
    category = "Tools"
    description = "Remote do braco robotico L-ONE"
    wants_status_bar = True

    def __init__(self):
        self._e = None
        self._peer = BCAST
        self._last = "--"
        self._rx = 0
        self._sent = 0
        self._ws_started = False
        self._seq = []
        self._playing = False
        self._seq_cancel = False
        self.cfg = {idx: {"name": n, "type": t, "min": mn, "max": mx,
                          "home": hm, "inv": False}
                    for idx, n, t, mn, mx, hm in SERVOS}
        self.mcfg = {m[0]: {"name": m[1], "inv": False} for m in MOTORS}
        self._load_cfg()
        self._angles = {idx: self.cfg[idx]["home"] for idx in self.cfg}
        self._sp = {idx: self.cfg[idx]["home"] for idx in self.cfg}
        self._msp = {m[0]: 0 for m in MOTORS}
        self._exp = {}     # ('s'/'m', idx) -> deadline ticks_ms do setpoint
        self._burst = {}   # {idx: n} re-envios de posicionais (fiabilidade)
        # dead reckoning: "s<idx>"/"m<idx>" -> posicao estimada (u)
        self._est = {"s%d" % i: 0.0 for i, c in self.cfg.items()
                     if c["type"] == "cont"}
        for m in MOTORS:
            self._est["m%d" % m[0]] = 0.0
        self._odo_t = time.ticks_ms()
        self._poses = {}
        self._home_pose = {}  # pose HOME configuravel (editavel; ref do sync)
        self._anims = {}   # nome -> [{"p": pose, "d": pausa ms}, ...]
        # assimetria direcional (motor DC nao anda o mesmo p/ cada lado):
        # ganho aplicado ao sentido NEGATIVO, aprendido por ida-e-volta
        self._dirgain = {}  # "m1" -> fator (default 1.0)
        self._acc = {}      # "m1+"/"m1-" -> integral RAW por sentido (p/ aprender)
        self._goto_on = False
        self._goto_cancel = False
        self._anim_on = False
        self._anim_cancel = False
        self._load_poses()

    # --- persistencia da afinacao (larm_cal.json) ---
    def _load_cfg(self):
        if not json:
            return
        try:
            with open("larm_cal.json") as f:
                d = json.load(f)
        except OSError:
            return
        except Exception as e:
            print("[larm] cal invalida:", e)
            return
        for k, v in d.get("servos", {}).items():
            idx = int(k)
            if idx not in self.cfg:
                continue
            c = self.cfg[idx]
            c["name"] = str(v.get("name", c["name"]))[:16]
            c["min"] = _clampi(v.get("min", c["min"]), HARD_MIN, HARD_MAX)
            c["max"] = _clampi(v.get("max", c["max"]), HARD_MIN, HARD_MAX)
            c["home"] = _clampi(v.get("home", c["home"]), HARD_MIN, HARD_MAX)
            c["inv"] = bool(v.get("inv", False))
        for k, v in d.get("motors", {}).items():
            midx = int(k)
            if midx in self.mcfg:
                self.mcfg[midx]["name"] = str(v.get("name", self.mcfg[midx]["name"]))[:16]
                self.mcfg[midx]["inv"] = bool(v.get("inv", False))
        print("[larm] afinacao carregada")

    def _save_cfg(self):
        if not json:
            return False
        try:
            out = {"servos": {str(i): {"name": c["name"], "min": c["min"],
                                       "max": c["max"], "home": c["home"],
                                       "inv": c["inv"]}
                              for i, c in self.cfg.items()},
                   "motors": {str(i): {"name": c["name"], "inv": c["inv"]}
                              for i, c in self.mcfg.items()}}
            with open("larm_cal.json", "w") as f:
                json.dump(out, f)
            return True
        except Exception as e:
            print("[larm] save falhou:", e)
            return False

    # --- poses (larm_poses.json) ---
    def _load_poses(self):
        if not json:
            return
        try:
            with open("larm_poses.json") as f:
                d = json.load(f)
            self._poses = d.get("poses", {})
            self._home_pose = d.get("home", {})
            self._anims = d.get("anims", {})
            self._dirgain = d.get("dirgain", {})
        except OSError:
            pass
        except Exception as e:
            print("[larm] poses invalidas:", e)

    def _save_poses(self):
        if not json:
            return False
        try:
            with open("larm_poses.json", "w") as f:
                json.dump({"poses": self._poses, "home": self._home_pose,
                           "anims": self._anims, "dirgain": self._dirgain}, f)
            return True
        except Exception as e:
            print("[larm] save poses falhou:", e)
            return False

    # --- ESP-NOW ---
    def _ensure_espnow(self):
        if self._e is not None:
            return self._e
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        e = espnow.ESPNow()
        e.active(True)
        self._peer = C3_MAC if C3_MAC else BCAST
        for p in (self._peer, BCAST):
            try:
                e.add_peer(p)
            except OSError:
                pass
        self._e = e
        return e

    def _channel(self):
        try:
            return network.WLAN(network.STA_IF).config("channel")
        except Exception:
            return "?"

    def _send(self, payload):
        try:
            e = self._ensure_espnow()
            e.send(self._peer, payload, False)
            self._sent += 1
            return True
        except Exception as ex:
            print("[larm] send falhou:", ex)
            return False

    def _move(self, idx, angle):
        angle = _clampi(angle, HARD_MIN, HARD_MAX)
        if self._send(MAGIC + b"M" + bytes([idx, angle])):
            self._angles[idx] = angle
            self._last = "s%d=%d" % (idx, angle)
        return angle

    def _motor(self, midx, pct):
        pct = _clampi(pct, -100, 100)
        if self._send(MAGIC + b"D" + bytes([midx, pct + 100])):
            self._last = "m%d=%d" % (midx, pct)

    # --- setpoints (a UNICA porta de entrada para movimento continuo) ---
    def _vel_angle(self, idx, dirn, speed):
        """dir -1/+1 + velocidade %% -> 'angulo' (=velocidade) do servo continuo."""
        c = self.cfg[idx]
        speed = _clampi(speed, 5, 100)
        if dirn > 0:
            return _clampi(c["home"] + (c["max"] - c["home"]) * speed // 100,
                           HARD_MIN, HARD_MAX)
        return _clampi(c["home"] - (c["home"] - c["min"]) * speed // 100,
                       HARD_MIN, HARD_MAX)

    def _set_vel(self, idx, dirn, speed=100, ms=500):
        c = self.cfg.get(idx)
        if not c or c["type"] != "cont":
            return
        if c["inv"]:
            dirn = -dirn
        now = time.ticks_ms()
        if dirn == 0:
            self._sp[idx] = c["home"]
            self._exp[("s", idx)] = time.ticks_add(now, 250)  # tail de paragem
        else:
            self._sp[idx] = self._vel_angle(idx, dirn, speed)
            self._exp[("s", idx)] = time.ticks_add(now, ms)

    def _set_motor_vel(self, midx, dirn, speed=100, ms=500):
        if midx not in self._msp:
            return
        mc = self.mcfg.get(midx)
        if mc and mc.get("inv"):
            dirn = -dirn
        now = time.ticks_ms()
        if dirn == 0:
            self._msp[midx] = 0
            self._exp[("m", midx)] = time.ticks_add(now, 250)
        else:
            self._msp[midx] = _clampi(dirn * speed, -100, 100)
            self._exp[("m", midx)] = time.ticks_add(now, ms)

    def _set_claw(self, idx, angle):
        c = self.cfg.get(idx)
        if not c or c["type"] != "pos":
            return None
        v = _clampi(angle, c["min"], c["max"])
        self._sp[idx] = v
        self._move(idx, v)
        self._burst[idx] = 4
        return v

    def _stop_all(self):
        """Para juntas + base. NAO mexe na garra (posicional segura)."""
        for idx, c in self.cfg.items():
            if c["type"] == "cont":
                self._set_vel(idx, 0)
        for midx in self._msp:
            self._set_motor_vel(midx, 0)
        self._last = "stop"

    def _home(self):
        self._stop_all()
        # pose HOME configuravel: HOME = ir la por dead reckoning
        if self._home_pose and self._start_goto(self._home_pose):
            self._last = "home"
            return
        for idx, c in self.cfg.items():
            if c["type"] == "pos":
                self._set_claw(idx, c["home"])
        self._last = "home"

    # --- WebSocket + stream ---
    def _ensure_ws(self):
        if self._ws_started:
            return
        self._ws_started = True
        try:
            asyncio.create_task(self._ws_serve())
            asyncio.create_task(self._stream())
        except Exception as e:
            self._ws_started = False
            print("[larm] ws start falhou:", e)

    async def _ws_serve(self):
        try:
            await asyncio.start_server(self._ws_handler, "0.0.0.0", WS_PORT)
            print("[larm] WebSocket :%d" % WS_PORT)
        except Exception as e:
            print("[larm] WS erro:", e)

    async def _stream(self):
        # ~30Hz: envia o setpoint de cada atuador ATIVO (fluxo constante ->
        # pacote perdido auto-corrige + alimenta o watchdog do C3). Expirou ->
        # neutro + tail 250ms -> silencio radio.
        # BEACON: broadcast b"LAB" ~4Hz, sempre. E o sinal que o C3 usa para
        # encontrar o canal (scan no boot / apos mudanca de canal do router).
        tick = 0
        while True:
            await asyncio.sleep_ms(33)
            now = time.ticks_ms()
            tick += 1
            # dead reckoning: integra a velocidade comandada NESTE momento.
            # Tudo (WS, HTTP, seq, goto) passa por _sp/_msp -> apanha tudo.
            dt = time.ticks_diff(now, self._odo_t)
            self._odo_t = now
            if 0 < dt <= 250:
                for idx, c in self.cfg.items():
                    if c["type"] == "cont":
                        d = self._sp[idx] - c["home"]
                        if d:
                            self._integ("s%d" % idx, d * dt / 1000)
                for midx, v in self._msp.items():
                    if v:
                        self._integ("m%d" % midx, v * dt / 1000)
            if tick % 8 == 0:
                # payload do beacon leva o CANAL atual: o C3 ouve-o (mesmo por
                # fuga de canal adjacente) e tuna direto para o canal certo.
                try:
                    ch = self._channel()
                    pl = MAGIC + b"B" + (bytes([ch]) if isinstance(ch, int)
                                         and 1 <= ch <= 14 else b"")
                    self._ensure_espnow().send(BCAST, pl, False)
                except Exception:
                    pass
            for idx, c in self.cfg.items():
                if c["type"] != "cont":
                    continue
                if time.ticks_diff(self._exp.get(("s", idx), 0), now) > 0:
                    self._move(idx, self._sp[idx])
                elif self._sp[idx] != c["home"]:
                    self._sp[idx] = c["home"]
                    self._exp[("s", idx)] = time.ticks_add(now, 250)
                    self._move(idx, c["home"])
            for midx in self._msp:
                if time.ticks_diff(self._exp.get(("m", midx), 0), now) > 0:
                    self._motor(midx, self._msp[midx])
                elif self._msp[midx] != 0:
                    self._msp[midx] = 0
                    self._exp[("m", midx)] = time.ticks_add(now, 250)
                    self._motor(midx, 0)
            for i in list(self._burst.keys()):
                if self._burst[i] > 0:
                    self._burst[i] -= 1
                    self._move(i, self._sp[i])
                else:
                    del self._burst[i]

    async def _ws_handler(self, reader, writer):
        try:
            key = None
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                if line[:18].lower() == b"sec-websocket-key:":
                    key = line.split(b":", 1)[1].strip()
            if not key:
                return
            accept = binascii.b2a_base64(
                hashlib.sha1(key + _WS_GUID).digest()).strip()
            writer.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                         b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            await writer.drain()
            # browser ligado -> larm toma o LCD. Importante p/ LATENCIA: a
            # larm e estatica (1 redraw/s); apps animadas (frog) fazem
            # draw_frame+show() a 15 FPS e cada blit SPI bloqueia o loop
            # asyncio ~50-150ms -> RTT do WS dispara se ficarem no ecra.
            self._take_lcd()
            try:
                sock = getattr(writer, "s", None)
                if sock is not None:
                    sock.setsockopt(6, 1, 1)  # TCP_NODELAY
            except Exception:
                pass
            while True:
                op, data = await _ws_frame(reader)
                if op == 0x8:
                    break
                if op == 0x9:
                    writer.write(b"\x8a\x00")
                    await writer.drain()
                    continue
                if op == 0x1:
                    self._ws_cmd(data.decode())
                    # ack leva a posicao estimada: coords ao vivo pelo WS
                    # durante o hold (e o poll HTTP pode abrandar)
                    try:
                        pl = "k" + json.dumps(self._snapshot()) if json else "k"
                    except Exception:
                        pl = "k"
                    writer.write(_ws_text_frame(pl))
                    await writer.drain()
        except Exception:
            pass
        finally:
            # browser foi-se embora: nao deixar nada a rodar a espera do expiry
            self._stop_all()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _ws_cmd(self, msg):
        self._rx += 1
        self._cancel_auto()        # comando manual > goto/animacao
        try:
            if msg == "h":
                self._home()
                return
            if msg == "s":
                self._stop_all()
                return
            parts = msg.split(",")
            tag = parts[0]
            if tag == "v":                      # junta: v,idx,dir[,speed]
                sp = int(parts[3]) if len(parts) > 3 else 100
                self._set_vel(int(parts[1]), int(parts[2]), sp)
            elif tag == "b":                    # base: b,midx,dir[,speed]
                sp = int(parts[3]) if len(parts) > 3 else 100
                self._set_motor_vel(int(parts[1]), int(parts[2]), sp)
            elif tag == "p":                    # garra: p,idx,angle
                self._set_claw(int(parts[1]), int(parts[2]))
            elif tag == "x":                    # stop 1 junta: x,idx
                self._set_vel(int(parts[1]), 0)
        except Exception:
            pass

    # --- sequencias (player device-side; timing local = fluido) ---
    async def _play(self):
        self._playing = True
        self._seq_cancel = False
        try:
            for _ in range(40):       # espera um goto a acabar largar
                if not self._goto_on:
                    break
                await asyncio.sleep_ms(50)
            for st in self._seq:
                if self._seq_cancel:
                    break
                a = st.get("a")
                i = st.get("i", 0)
                v = st.get("v", 0)
                d = _clampi(st.get("d", 500), 0, 10000)
                now = time.ticks_ms()
                if a == "p":                    # garra: angulo
                    self._set_claw(i, v)
                elif a == "c" and i in self.cfg:  # junta: "angulo"=vel por d ms
                    self._sp[i] = _clampi(v, HARD_MIN, HARD_MAX)
                    self._exp[("s", i)] = time.ticks_add(now, d)
                elif a == "m" and i in self._msp:  # motor: pct por d ms
                    self._msp[i] = _clampi(v, -100, 100)
                    self._exp[("m", i)] = time.ticks_add(now, d)
                elif a == "h":
                    self._home()
                await asyncio.sleep_ms(d)
        finally:
            self._stop_all()
            self._playing = False

    def _integ(self, k, raw):
        """integra movimento na estimativa; sentido negativo leva o ganho
        aprendido. Acumula o RAW por sentido (para o /callearn)."""
        if raw > 0:
            self._acc[k + "+"] = self._acc.get(k + "+", 0.0) + raw
            self._est[k] += raw
        else:
            self._acc[k + "-"] = self._acc.get(k + "-", 0.0) - raw
            self._est[k] += raw * self._dirgain.get(k, 1.0)

    # --- goto (loop fechado sobre a posicao ESTIMADA) ---
    def _cancel_auto(self):
        """comando manual > tudo o que e automatico (goto + animacao)."""
        self._goto_cancel = True
        self._anim_cancel = True

    def _goto_target(self, target):
        """Filtra um alvo: {"s1": u, "s2": u, "m1": u, "p3": angulo}."""
        t = {}
        for k, v in target.items():
            try:
                if k in self._est:
                    t[k] = float(v)
                elif (k[:1] == "p" and int(k[1:]) in self.cfg
                      and self.cfg[int(k[1:])]["type"] == "pos"):
                    t[k] = int(v)
            except Exception:
                pass
        return t

    def _snapshot(self):
        """pose = estimativa atual + angulo real da garra."""
        p = {k: round(v, 1) for k, v in self._est.items()}
        for i, c in self.cfg.items():
            if c["type"] == "pos":
                p["p%d" % i] = self._sp[i]
        return p

    def _start_goto(self, target):
        t = self._goto_target(target)
        if not t:
            return False
        self._seq_cancel = True
        self._cancel_auto()
        try:
            asyncio.create_task(self._goto(t))
            return True
        except Exception as e:
            print("[larm] goto falhou:", e)
            return False

    async def _goto(self, target):
        # espera o goto/seq anterior largar (ja levaram cancel)
        for _ in range(40):
            if not self._goto_on and not self._playing:
                break
            await asyncio.sleep_ms(50)
        self._goto_on = True
        self._goto_cancel = False
        t0 = time.ticks_ms()
        # base: se o alvo esta ATRAS, recuar GOTO_BACK_U alem dele e chegar
        # sempre no sentido + (mata folga/assimetria do motor DC). rev[m]=True
        # enquanto estiver na fase de recuo.
        rev = {}
        for m in self._msp:
            k = "m%d" % m
            if k in target and target[k] < self._est[k] - GOTO_EPS_M:
                rev[m] = True
        try:
            for i, c in self.cfg.items():
                if c["type"] == "pos" and ("p%d" % i) in target:
                    self._set_claw(i, target["p%d" % i])
            while (not self._goto_cancel and
                   time.ticks_diff(time.ticks_ms(), t0) < GOTO_CAP_MS):
                now = time.ticks_ms()
                busy = False
                for i, c in self.cfg.items():
                    if c["type"] != "cont" or ("s%d" % i) not in target:
                        continue
                    err = target["s%d" % i] - self._est["s%d" % i]
                    if err > GOTO_EPS_J or err < -GOTO_EPS_J:
                        busy = True
                        d = (c["max"] if err > 0 else c["min"]) - c["home"]
                        self._sp[i] = _clampi(c["home"] + d * GOTO_SPD // 100,
                                              HARD_MIN, HARD_MAX)
                        self._exp[("s", i)] = time.ticks_add(now, 300)
                    elif self._sp[i] != c["home"]:
                        self._sp[i] = c["home"]
                        self._exp[("s", i)] = time.ticks_add(now, 250)
                for m in self._msp:
                    k = "m%d" % m
                    if k not in target:
                        continue
                    err = target[k] - self._est[k]
                    if rev.get(m):
                        # fase recuo: passar GOTO_BACK_U alem do alvo
                        if err < GOTO_BACK_U:
                            busy = True
                            self._msp[m] = -GOTO_PCT
                            self._exp[("m", m)] = time.ticks_add(now, 300)
                            continue
                        rev[m] = False    # ja atras -> aproximacao em +
                    if err > GOTO_EPS_M:
                        busy = True
                        self._msp[m] = (GOTO_PCT if err > GOTO_NEAR_U
                                        else GOTO_PCT_NEAR)
                        self._exp[("m", m)] = time.ticks_add(now, 300)
                    elif err < -GOTO_EPS_M:   # passou-se: recua outra vez
                        rev[m] = True
                        busy = True
                    elif self._msp[m] != 0:
                        self._msp[m] = 0
                        self._exp[("m", m)] = time.ticks_add(now, 250)
                if not busy:
                    break
                await asyncio.sleep_ms(GOTO_TICK_MS)
            self._last = "goto x" if self._goto_cancel else "goto ok"
        finally:
            self._stop_all()
            self._goto_on = False

    # --- animacoes de poses (goto encadeado + pausas) ---
    def _start_anim(self, name):
        steps = self._anims.get(name)
        if not steps:
            return False
        self._seq_cancel = True
        self._cancel_auto()
        try:
            asyncio.create_task(self._anim_run(list(steps)))
            return True
        except Exception as e:
            print("[larm] anim falhou:", e)
            return False

    async def _anim_run(self, steps):
        # espera o anterior largar (cancel ja foi pedido em _start_anim)
        for _ in range(40):
            if not self._anim_on:
                break
            await asyncio.sleep_ms(50)
        self._anim_on = True
        self._anim_cancel = False
        try:
            for st in steps:
                if self._anim_cancel:
                    break
                p = self._poses.get(st.get("p"))
                if p:
                    t = self._goto_target(p)
                    if t:
                        await self._goto(t)
                        if self._goto_cancel:   # cancelado a meio do goto
                            break
                d = _clampi(st.get("d", 400), 0, 10000)
                t0 = time.ticks_ms()
                while (not self._anim_cancel and
                       time.ticks_diff(time.ticks_ms(), t0) < d):
                    await asyncio.sleep_ms(50)
            self._last = "anim x" if self._anim_cancel else "anim ok"
        finally:
            self._anim_on = False

    # --- Web ---
    def _take_lcd(self):
        try:
            from core import os as kernel
            if kernel.current() is not self:
                kernel.launch(self.name)
        except Exception:
            pass

    def _http_ms(self, params):
        try:
            return _clampi(int(params.get("ms", HTTP_MS_DEFAULT)), 50, HTTP_MS_CAP)
        except Exception:
            return HTTP_MS_DEFAULT

    def on_web(self, method, subpath, params, body):
        self._ensure_ws()
        if subpath != "state":     # o poll de estado nao rouba o LCD
            self._take_lcd()
        if subpath in ("move", "motor", "stop", "stopall", "home", "seq"):
            self._cancel_auto()        # comando manual/seq > goto/animacao
        if subpath == "move":
            # garra: vai e fica. Juntas continuas: "angle"=velocidade durante
            # ms (default 1500, cap 3000) e PARA sozinho -- nunca fica a rodar.
            idx = int(params.get("idx", "0"))
            c = self.cfg.get(idx)
            if not c:
                return {"ok": False, "err": "idx"}
            angle = int(params.get("angle", "90"))
            if c["type"] == "pos":
                return {"ok": True, "angle": self._set_claw(idx, angle)}
            ms = self._http_ms(params)
            self._sp[idx] = _clampi(angle, HARD_MIN, HARD_MAX)
            self._exp[("s", idx)] = time.ticks_add(time.ticks_ms(), ms)
            return {"ok": True, "angle": self._sp[idx], "ms": ms}
        if subpath == "motor":
            midx = int(params.get("idx", "0"))
            if midx not in self._msp:
                return {"ok": False, "err": "idx"}
            pct = _clampi(int(params.get("pct", "0")), -100, 100)
            if pct == 0:
                self._set_motor_vel(midx, 0)
                return {"ok": True, "pct": 0}
            ms = self._http_ms(params)
            self._msp[midx] = pct
            self._exp[("m", midx)] = time.ticks_add(time.ticks_ms(), ms)
            return {"ok": True, "pct": pct, "ms": ms}
        if subpath == "stop":
            idx = int(params.get("idx", "0"))
            if idx in self.cfg:
                self._set_vel(idx, 0)
            return {"ok": True}
        if subpath == "stopall":
            self._stop_all()
            return {"ok": True}
        if subpath == "home":
            self._home()
            return {"ok": True}
        if subpath == "config":
            servos = [{"idx": i, "name": c["name"], "type": c["type"],
                       "min": c["min"], "max": c["max"], "home": c["home"],
                       "inv": c["inv"]} for i, c in sorted(self.cfg.items())]
            motors = [{"idx": i, "name": c["name"], "inv": c["inv"]}
                      for i, c in sorted(self.mcfg.items())]
            return {"hard": [HARD_MIN, HARD_MAX], "channel": self._channel(),
                    "ws": WS_PORT, "servos": servos, "motors": motors,
                    "rx": self._rx, "sent": self._sent}
        if subpath == "save":
            if not json:
                return {"ok": False}
            try:
                d = json.loads(body.decode() if isinstance(
                    body, (bytes, bytearray)) else body)
            except Exception as e:
                return {"ok": False, "err": str(e)}
            for sv in d.get("servos", []):
                idx = int(sv.get("idx", 0))
                if idx not in self.cfg:
                    continue
                c = self.cfg[idx]
                c["name"] = str(sv.get("name", c["name"]))[:16]
                c["inv"] = bool(sv.get("inv", c["inv"]))
                c["home"] = _clampi(sv.get("home", c["home"]), HARD_MIN, HARD_MAX)
                mn = _clampi(sv.get("min", c["min"]), HARD_MIN, HARD_MAX)
                mx = _clampi(sv.get("max", c["max"]), HARD_MIN, HARD_MAX)
                c["min"], c["max"] = (mn, mx) if mx != mn else (mn, mn + 1)
            for mo in d.get("motors", []):
                midx = int(mo.get("idx", 0))
                if midx in self.mcfg:
                    self.mcfg[midx]["name"] = str(mo.get("name", self.mcfg[midx]["name"]))[:16]
                    self.mcfg[midx]["inv"] = bool(mo.get("inv", self.mcfg[midx]["inv"]))
            return {"ok": self._save_cfg()}
        if subpath == "seq":
            if not json:
                return {"ok": False}
            try:
                self._seq = json.loads(body.decode() if isinstance(
                    body, (bytes, bytearray)) else body).get("steps", [])[:80]
            except Exception as e:
                return {"ok": False, "err": str(e)}
            if not self._playing:
                try:
                    asyncio.create_task(self._play())
                except Exception as e:
                    return {"ok": False, "err": str(e)}
            return {"ok": True, "steps": len(self._seq)}
        if subpath == "seqstop":
            self._seq_cancel = True
            self._cancel_auto()
            self._stop_all()
            return {"ok": True}
        # --- dead reckoning / poses ---
        if subpath == "state":
            est = {k: round(v, 1) for k, v in self._est.items()}
            for i, c in self.cfg.items():
                if c["type"] == "pos":
                    est["p%d" % i] = self._sp[i]
            return {"est": est, "goto": self._goto_on, "home": self._home_pose,
                    "anim": self._anim_on, "gain": self._dirgain,
                    "poses": sorted(self._poses.keys(), key=lambda s: s.lower()),
                    "anims": sorted(self._anims.keys(), key=lambda s: s.lower())}
        if subpath == "zero":
            for k in self._est:
                self._est[k] = 0.0
            self._acc = {}
            self._last = "zero"
            return {"ok": True}
        if subpath == "callearn":
            # ida-e-volta: o utilizador voltou a por o eixo FISICAMENTE no
            # inicio. Num caminho fechado a distancia real e igual nos 2
            # sentidos -> ganho do sentido negativo = raw+ / raw-.
            k = str(params.get("axis", "m1"))
            if k not in self._est:
                return {"ok": False, "err": "axis"}
            p = self._acc.get(k + "+", 0.0)
            n = self._acc.get(k + "-", 0.0)
            if p < 5 or n < 5:
                return {"ok": False,
                        "err": "anda primeiro (ida e volta; +%.0f/-%.0f)" % (p, n)}
            g = p / n
            if g < 0.4 or g > 2.5:
                return {"ok": False, "err": "razao %.2f estranha -- refaz" % g}
            self._dirgain[k] = round(g, 3)
            self._est[k] = 0.0          # esta fisicamente no inicio
            self._acc.pop(k + "+", None)
            self._acc.pop(k + "-", None)
            self._last = "gain %s=%.2f" % (k, g)
            return {"ok": self._save_poses(), "axis": k, "gain": self._dirgain[k]}
        if subpath == "homeset":
            # "a pose HOME passa a ser ESTA posicao"
            self._home_pose = self._snapshot()
            return {"ok": self._save_poses(), "home": self._home_pose}
        if subpath == "homesync":
            # "pus o braco FISICAMENTE na pose home" -> o software adota as
            # coordenadas do home (substitui o antigo ZERO; conserta todas as
            # poses de uma vez porque partilham o referencial)
            if not self._home_pose:
                return {"ok": False, "err": "define o HOME primeiro"}
            for k in self._est:
                if k in self._home_pose:
                    try:
                        self._est[k] = float(self._home_pose[k])
                    except Exception:
                        pass
            self._acc = {}
            self._last = "sync"
            return {"ok": True}
        if subpath == "homesave":
            # POST {"vals": {...}} -- editar os valores do home a mao
            if not json:
                return {"ok": False}
            try:
                vals = json.loads(body.decode() if isinstance(
                    body, (bytes, bytearray)) else body).get("vals", {})
            except Exception as e:
                return {"ok": False, "err": str(e)}
            t = self._goto_target(vals)
            if not t:
                return {"ok": False, "err": "vals"}
            self._home_pose = t
            return {"ok": self._save_poses(), "home": self._home_pose}
        if subpath in ("posesave", "posego", "posedel", "poseget"):
            d0 = {}
            if subpath == "posesave" and body:
                # POST {"name":x, "vals":{...}} = editar valores a mao;
                # sem body (GET) = snapshot da posicao atual
                try:
                    d0 = json.loads(body.decode() if isinstance(
                        body, (bytes, bytearray)) else body)
                except Exception:
                    d0 = {}
            nm = str(params.get("name", "") or d0.get("name", ""))
            name = "".join(ch for ch in nm[:12]
                           if ch.isalpha() or ch.isdigit() or ch in "_-")
            if not name:
                return {"ok": False, "err": "name"}
            if subpath == "poseget":
                p = self._poses.get(name)
                return {"ok": p is not None, "name": name, "pose": p or {}}
            if subpath == "posego":
                p = self._poses.get(name)
                if not p:
                    return {"ok": False, "err": "desconhecida"}
                return {"ok": self._start_goto(p), "pose": p}
            if subpath == "posedel":
                if self._poses.pop(name, None) is None:
                    return {"ok": False, "err": "desconhecida"}
                return {"ok": self._save_poses()}
            if name not in self._poses and len(self._poses) >= POSES_MAX:
                return {"ok": False, "err": "cheio (max %d)" % POSES_MAX}
            if d0.get("vals"):
                p = self._goto_target(d0["vals"])
                if not p:
                    return {"ok": False, "err": "vals"}
            else:
                p = self._snapshot()
            self._poses[name] = p
            return {"ok": self._save_poses(), "pose": p}
        if subpath in ("animsave", "animgo", "animdel", "animget"):
            d0 = {}
            if subpath == "animsave":
                # POST {"name":x, "steps":[{"p":"pose","d":pausa_ms}, ...]}
                if not json:
                    return {"ok": False}
                try:
                    d0 = json.loads(body.decode() if isinstance(
                        body, (bytes, bytearray)) else body)
                except Exception as e:
                    return {"ok": False, "err": str(e)}
            nm = str(params.get("name", "") or d0.get("name", ""))
            name = "".join(ch for ch in nm[:12]
                           if ch.isalpha() or ch.isdigit() or ch in "_-")
            if not name:
                return {"ok": False, "err": "name"}
            if subpath == "animget":
                st = self._anims.get(name)
                return {"ok": st is not None, "name": name, "steps": st or []}
            if subpath == "animgo":
                return {"ok": self._start_anim(name)}
            if subpath == "animdel":
                if self._anims.pop(name, None) is None:
                    return {"ok": False, "err": "desconhecida"}
                return {"ok": self._save_poses()}
            raw = d0.get("steps", [])
            steps = [{"p": str(st.get("p", ""))[:12],
                      "d": _clampi(st.get("d", 400), 0, 10000)}
                     for st in raw[:ANIM_STEPS_MAX] if st.get("p")]
            if not steps:
                return {"ok": False, "err": "steps"}
            if name not in self._anims and len(self._anims) >= ANIMS_MAX:
                return {"ok": False, "err": "cheio (max %d)" % ANIMS_MAX}
            self._anims[name] = steps
            return {"ok": self._save_poses(), "steps": len(steps)}
        if subpath == "goto":
            # POST JSON direto {"s1":u,"s2":u,"m1":u,"p3":ang} (fase AI)
            if not json:
                return {"ok": False}
            try:
                t = json.loads(body.decode() if isinstance(
                    body, (bytes, bytearray)) else body)
            except Exception as e:
                return {"ok": False, "err": str(e)}
            return {"ok": self._start_goto(t)}
        return _PAGE

    def on_draw(self, lcd):
        sb = display.status_bar_height()
        lcd.fb.fill_rect(0, sb, lcd.width, lcd.height - sb, 0)
        y = sb + 10
        display.text_scaled(lcd, "L-ONE", 8, y, 0xFFFF, scale=2)
        y += 28
        for idx in sorted(self.cfg):
            display.text_scaled(lcd, "%s:%d" % (self.cfg[idx]["name"][:8],
                                self._angles.get(idx, 0)), 8, y,
                                lcd.color(0, 255, 0), scale=1)
            y += 14
        display.text_scaled(lcd, "ch%s %s" % (self._channel(), self._last),
                            8, y + 6, lcd.color(150, 150, 150), scale=1)


_PAGE = """<!doctype html><html lang="pt"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#131210">
<title>L-ONE</title>
</head><body>
<style>
/* paleta retro americana: creme #e8d8c9 / azul #4b607f / laranja #f3701e */
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:#131210;--card:#211e1a;--cream:#e8d8c9;--blue:#4b607f;--orange:#f3701e;--ink:#17150f;--muted:#9a8f81}
body{margin:0;background:var(--bg);color:var(--cream);font-family:-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:540px;margin:0 auto;padding:14px 14px 112px}
header{display:flex;justify-content:space-between;align-items:center;margin:8px 2px 16px}
h1{font-size:1.85rem;font-weight:900;letter-spacing:-.045em;margin:0;color:var(--cream)}
h1 i{font-style:normal;color:var(--orange)}
#pill{font-size:.68rem;font-weight:700;letter-spacing:.02em;padding:.5rem .85rem;border-radius:999px;background:var(--card);color:var(--cream);white-space:nowrap}
#pill .dot{display:inline-block;width:.55rem;height:.55rem;border-radius:50%;background:var(--orange);margin-right:.4rem;vertical-align:middle}
#pill.ok .dot{background:#9bb167}
.tabs{display:flex;gap:6px;background:var(--card);padding:6px;border-radius:18px;margin-bottom:14px}
.tabs button{flex:1;padding:.72rem 0;font-size:.74rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;background:transparent;color:var(--muted);border:none;border-radius:13px;cursor:pointer}
.tabs button.on{background:var(--cream);color:var(--ink)}
.tab{display:none}.tab.on{display:block}
.ck{border-radius:22px;padding:16px;margin-bottom:12px}
.ck-c{background:var(--cream);color:var(--ink)}
.ck-b{background:var(--blue);color:var(--cream)}
.ck-o{background:var(--orange);color:var(--ink)}
.hd{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.75rem}
.hd b{font-size:1.02rem;font-weight:900;letter-spacing:.01em;text-transform:uppercase}
.hint{font-size:.62rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase;opacity:.55}
.btns{display:flex;gap:10px}
.btns button{flex:1;min-height:68px;font-size:1.5rem;line-height:1;font-weight:900;background:var(--ink);color:var(--cream);border:none;border-radius:16px;cursor:pointer;touch-action:none;user-select:none;-webkit-user-select:none;transition:transform .05s,background .08s}
.btns button:active{transform:scale(.96)}
.btns button.on{background:var(--orange);color:var(--ink)}
.ck-o .btns button.on{background:var(--cream)}
.ck-o .btns button{font-size:1rem;letter-spacing:.05em;text-transform:uppercase;font-weight:800}
.val{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:800;font-size:1.2rem}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:48px;background:transparent;margin-top:8px;touch-action:none}
input[type=range]::-webkit-slider-runnable-track{height:14px;border-radius:8px;background:var(--ink)}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:34px;height:34px;border-radius:50%;background:var(--cream);border:5px solid var(--ink);margin-top:-10px}
input[type=range]::-moz-range-track{height:14px;border-radius:8px;background:var(--ink)}
input[type=range]::-moz-range-thumb{width:24px;height:24px;border-radius:50%;background:var(--cream);border:5px solid var(--ink)}
.seg{display:flex;gap:6px;background:var(--card);padding:6px;border-radius:16px;margin-bottom:12px}
.seg button{flex:1;padding:.72rem 0;font-size:.72rem;font-weight:800;letter-spacing:.05em;text-transform:uppercase;background:transparent;color:var(--muted);border:none;border-radius:11px;cursor:pointer}
.seg button.on{background:var(--blue);color:var(--cream)}
.bottom{position:fixed;left:0;right:0;bottom:0;z-index:9;display:flex;gap:10px;max-width:540px;margin:0 auto;padding:14px 14px calc(14px + env(safe-area-inset-bottom));background:linear-gradient(transparent,var(--bg) 40%);pointer-events:none}
.bottom button{flex:1;padding:1.05rem 0;font-size:.95rem;font-weight:900;letter-spacing:.05em;text-transform:uppercase;border:none;border-radius:18px;cursor:pointer;pointer-events:auto}
#sa{background:var(--orange);color:var(--ink);flex:1.4;box-shadow:0 6px 26px rgba(243,112,30,.4)}
#hh{background:var(--cream);color:var(--ink)}
.prow{display:flex;gap:8px;margin-top:10px}
.prow button{padding:.7rem .9rem;font-size:.76rem;font-weight:800;letter-spacing:.02em;border:none;border-radius:12px;background:var(--ink);color:var(--cream);cursor:pointer;white-space:nowrap}
.prow button.del{background:transparent;border:2px solid currentColor;color:inherit;opacity:.55}
.prow input,.prow select{flex:1;min-width:0;padding:.65rem .75rem;font-size:.92rem;font-weight:600;border:none;border-radius:12px;background:rgba(0,0,0,.18);color:inherit}
.ck-c .prow input,.ck-c .prow select{background:rgba(23,21,15,.09)}
.prow input::placeholder{color:inherit;opacity:.45}
.prow input[type=number]{flex:0 0 5.4rem}
.list{margin-top:12px}
.li{display:flex;align-items:center;gap:8px;background:rgba(0,0,0,.18);border-radius:13px;padding:8px 10px;margin-bottom:8px}
.ck-c .li{background:rgba(23,21,15,.09)}
.li b{flex:1;font-size:.95rem;font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li button{border:none;border-radius:10px;background:var(--ink);color:var(--cream);font-weight:800;font-size:.95rem;padding:.55rem .85rem;cursor:pointer}
.li button.sm{background:transparent;border:2px solid currentColor;color:inherit;opacity:.5;font-size:.74rem;padding:.42rem .6rem}
.dt{background:rgba(0,0,0,.18);border-radius:13px;padding:11px 12px;margin:-2px 0 10px}
.ck-c .dt{background:rgba(23,21,15,.09)}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.kv label{display:flex;align-items:center;gap:7px;font-size:.66rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase;opacity:.85}
.kv input{flex:1;width:100%;min-width:0;padding:.5rem .6rem;font-size:.92rem;font-weight:700;border:none;border-radius:10px;background:rgba(0,0,0,.2);color:inherit;font-family:ui-monospace,Menlo,Consolas,monospace}
.ck-c .kv input{background:rgba(23,21,15,.1)}
.mono,#asteps{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.8rem;margin-top:10px;line-height:1.7;background:rgba(0,0,0,.16);padding:10px 13px;border-radius:12px;word-break:break-word}
#gst{display:none;position:fixed;left:50%;transform:translateX(-50%);bottom:96px;z-index:8;padding:.7rem 1rem;border-radius:14px;background:var(--ink);color:var(--orange);font-size:.78rem;font-weight:800;white-space:nowrap;box-shadow:0 8px 24px rgba(0,0,0,.5)}
.note{font-size:.72rem;line-height:1.5;opacity:.6;margin:.65rem 2px 0}
</style>
<main>
<header><h1>L<i>&middot;</i>ONE</h1><span id="pill"><span class="dot"></span><span id="st">a ligar&hellip;</span></span></header>
<div class="tabs"><button id="tb0">Controlo</button><button id="tb1">Poses</button><button id="tb2">Anima&ccedil;&otilde;es</button></div>
<div class="tab" id="tab0">
<div class="seg" id="spd"></div>
<div class="mono" id="cpos2" style="margin:0 0 12px">&hellip;</div>
<div id="ctl"></div>
</div>
<div class="tab" id="tab1">
<section class="ck ck-b">
<div class="hd"><b>Home</b><span class="hint">pose de arranque</span></div>
<div class="mono" id="cpos">&hellip;</div>
<div class="prow"><button id="hsync">ESTOU no home</button><button id="hset">home = aqui</button><button id="hedit" class="del">editar</button></div>
<div class="dt" id="hdt" style="display:none;margin-top:10px"></div>
<p class="note" style="margin:.55rem 0 0">o bot&atilde;o HOME vai para esta pose. <b>ESTOU no home</b> = puseste o bra&ccedil;o fisicamente nela (ex.: depois de reiniciar o S3, ou quando a estimativa derivou) e o software adota essas coordenadas &mdash; conserta todas as poses de uma vez, porque partilham o referencial.</p>
</section>
<section class="ck ck-c" id="poses">
<div class="hd"><b>Poses</b><span class="hint">ir &middot; editar &middot; apagar</span></div>
<div class="prow"><input type=text id="pn" placeholder="nome da pose" maxlength=12><button id="ps">guardar atual</button></div>
<div class="list" id="pl"></div>
</section>
</div>
<div class="tab" id="tab2">
<section class="ck ck-c">
<div class="hd"><b>Nova</b><span class="hint">poses encadeadas</span></div>
<div class="prow"><select id="asel"></select><input type=number id="ad" value="400" min="0" max="10000" step="100" title="pausa depois (ms)"><button id="aadd">+ passo</button></div>
<div id="asteps">(sem passos)</div>
<div class="prow"><input type=text id="an" placeholder="nome da anima&ccedil;&atilde;o" maxlength=12><button id="asave">guardar</button><button id="aclr" class="del">limpar</button></div>
<p class="note" style="margin:.55rem 0 0">escolhe uma pose, define a pausa (ms) a seguir e "+ passo"; repete e guarda. A anima&ccedil;&atilde;o vai pose a pose por dead reckoning; STOP TUDO / qualquer comando manual cancela.</p>
</section>
<section class="ck ck-b">
<div class="hd"><b>Guardadas</b></div>
<div class="list" id="al"></div>
</section>
</div>
<div id="gst">a executar&hellip; (STOP TUDO cancela)</div>
<p class="note">teclado: &larr;/&rarr; base &middot; q/a + w/s juntas &middot; e/d garra &middot; espa&ccedil;o = stop</p>
</main>
<div class="bottom"><button id="sa">STOP TUDO</button><button id="hh">HOME</button></div>
<script>
const $=x=>document.getElementById(x);
let CFG=[],MOT=[],WSP=81,CH='?',ws=null,ok=false,T0=0,RTT='';
let HB={},KEY={},KMAP={},GK={};
let SPD=parseInt(localStorage.larmspd||'60');
let POSES=[],NM={},ANIMS=[],AS=[],HOMEP={},OPENP='',OPENA='';
function pill(){const p=$('pill');p.className=ok?'ok':'';
  $('st').textContent=ok?('ligado \\u00b7 ch'+CH+(RTT?' \\u00b7 '+RTT:'')):'sem WS \\u00b7 modo HTTP';}
function wsConnect(){
  try{ws=new WebSocket('ws://'+location.hostname+':'+WSP);
    ws.onopen=()=>{ok=true;RTT='';pill();};
    ws.onclose=()=>{ok=false;pill();setTimeout(wsConnect,1200);};
    ws.onerror=()=>{try{ws.close();}catch(e){}};
    ws.onmessage=ev=>{if(T0){RTT=Math.round(performance.now()-T0)+'ms';pill();T0=0;}
      if(ev.data&&ev.data.length>1){try{drawCp(JSON.parse(ev.data.slice(1)));}catch(_){}}};
  }catch(e){setTimeout(wsConnect,1200);}}
function tx(s){if(ok){try{T0=performance.now();ws.send(s);return true;}catch(e){}}return false;}
setInterval(()=>{for(const k in HB)HB[k]();},300);  /* heartbeat: renova expiry no S3 */
function hold(id,msg,stopMsg,fb,fbStop){const el=$(id);if(!el)return;
  el.addEventListener('contextmenu',e=>e.preventDefault());
  const down=e=>{if(HB[id])return;
    if(e){e.preventDefault();try{el.setPointerCapture(e.pointerId);}catch(_){}}
    el.classList.add('on');HB[id]=()=>{if(!tx(msg())&&fb)fb();};HB[id]();};
  const end=()=>{if(!HB[id])return;delete HB[id];el.classList.remove('on');
    if(!tx(stopMsg)&&fbStop)fbStop();};
  el.addEventListener('pointerdown',down);
  ['pointerup','pointercancel','lostpointercapture'].forEach(ev=>el.addEventListener(ev,end));
  KEY[id]={down:()=>down(null),up:end};}
function stopAll(){if(!tx('s'))fetch('/app/larm/stopall').catch(()=>{});
  for(const k in HB){delete HB[k];const el=$(k);if(el)el.classList.remove('on');}}
function row(n,id,hint,ck){return '<div class="ck '+ck+'"><div class="hd"><b>'+n+'</b><span class="hint">'+(hint||'')+'</span></div>'+
  '<div class="btns"><button id="'+id+'l">&#9664;</button><button id="'+id+'r">&#9654;</button></div></div>';}
let _cT=0,_cTo=null;
function clawSend(i,v){const go=()=>{_cT=performance.now();
    if(!tx('p,'+i+','+v))fetch('/app/larm/move?idx='+i+'&angle='+v).catch(()=>{});};
  if(performance.now()-_cT>=70)go();else{clearTimeout(_cTo);_cTo=setTimeout(go,70);}}
function vAng(s,d){return d>0?s.home+Math.round((s.max-s.home)*SPD/100):s.home-Math.round((s.home-s.min)*SPD/100);}
async function load(){
  const j=await (await fetch('/app/larm/config')).json();
  CFG=j.servos;MOT=j.motors||[];WSP=j.ws||81;CH=j.channel;
  pill();render();}
function render(){
  NM={};MOT.forEach(m=>NM['m'+m.idx]=m.name);
  CFG.forEach(s=>NM[(s.type==='cont'?'s':'p')+s.idx]=s.name);
  let h='';
  MOT.forEach(m=>{h+=row(m.name,'b'+m.idx,'gira','ck-c');});
  CFG.forEach(s=>{if(s.type==='cont')h+=row(s.name,'j'+s.idx,'junta','ck-b');});
  CFG.forEach(s=>{if(s.type==='pos'){
    h+='<div class="ck ck-o"><div class="hd"><b>'+s.name+'</b><span><span class="val" id="gv'+s.idx+'">'+s.home+'</span>&deg;</span></div>'+
    '<div class="btns"><button id="gc'+s.idx+'">fechar</button><button id="go'+s.idx+'">abrir</button></div>'+
    '<input type=range id="gsl'+s.idx+'" min='+s.min+' max='+s.max+' value='+s.home+'>'+'</div>';}});
  $('ctl').innerHTML=h;
  $('spd').innerHTML=[[30,'lento'],[60,'m\\u00e9dio'],[100,'r\\u00e1pido']].map(o=>
    '<button data-v="'+o[0]+'"'+(SPD===o[0]?' class="on"':'')+'>'+o[1]+'</button>').join('');
  $('spd').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{
    SPD=parseInt(b.dataset.v);localStorage.larmspd=SPD;
    $('spd').querySelectorAll('button').forEach(x=>x.className=x===b?'on':'');}));
  MOT.forEach(m=>{const i=m.idx;
    hold('b'+i+'l',()=>'b,'+i+',-1,'+SPD,'b,'+i+',0',
      ()=>fetch('/app/larm/motor?idx='+i+'&pct=-'+SPD+'&ms=500').catch(()=>{}),
      ()=>fetch('/app/larm/motor?idx='+i+'&pct=0').catch(()=>{}));
    hold('b'+i+'r',()=>'b,'+i+',1,'+SPD,'b,'+i+',0',
      ()=>fetch('/app/larm/motor?idx='+i+'&pct='+SPD+'&ms=500').catch(()=>{}),
      ()=>fetch('/app/larm/motor?idx='+i+'&pct=0').catch(()=>{}));});
  CFG.forEach(s=>{const i=s.idx;
    if(s.type==='cont'){
      hold('j'+i+'l',()=>'v,'+i+',-1,'+SPD,'v,'+i+',0',
        ()=>fetch('/app/larm/move?idx='+i+'&angle='+vAng(s,-1)+'&ms=500').catch(()=>{}),
        ()=>fetch('/app/larm/stop?idx='+i).catch(()=>{}));
      hold('j'+i+'r',()=>'v,'+i+',1,'+SPD,'v,'+i+',0',
        ()=>fetch('/app/larm/move?idx='+i+'&angle='+vAng(s,1)+'&ms=500').catch(()=>{}),
        ()=>fetch('/app/larm/stop?idx='+i).catch(()=>{}));
    }else{
      const sl=$('gsl'+i),gv=$('gv'+i);
      const setv=v=>{v=Math.max(s.min,Math.min(s.max,v));sl.value=v;gv.textContent=v;clawSend(i,v);};
      sl.addEventListener('input',()=>{gv.textContent=sl.value;clawSend(i,parseInt(sl.value));});
      $('gc'+i).addEventListener('click',()=>setv(s.min));
      $('go'+i).addEventListener('click',()=>setv(s.max));
      GK.KeyE=()=>setv(parseInt(sl.value)+4);
      GK.KeyD=()=>setv(parseInt(sl.value)-4);}});
  $('sa').addEventListener('click',stopAll);
  $('hh').addEventListener('click',()=>{if(!tx('h'))fetch('/app/larm/home').catch(()=>{});});
  if(MOT[0]){KMAP.ArrowLeft='b'+MOT[0].idx+'l';KMAP.ArrowRight='b'+MOT[0].idx+'r';}
  const conts=CFG.filter(s=>s.type==='cont');
  const pares=[['KeyQ','KeyA'],['KeyW','KeyS']];
  conts.slice(0,2).forEach((s,n)=>{KMAP[pares[n][0]]='j'+s.idx+'l';KMAP[pares[n][1]]='j'+s.idx+'r';});}
function typing(e){const t=e.target;return t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA');}
document.addEventListener('keydown',e=>{
  if(typing(e))return;  /* a escrever num campo: teclado NAO mexe o braco */
  if(e.code==='Space'){e.preventDefault();stopAll();return;}
  if(GK[e.code]){e.preventDefault();GK[e.code]();return;}
  if(e.repeat)return;
  const id=KMAP[e.code];if(id&&KEY[id]){e.preventDefault();KEY[id].down();}});
document.addEventListener('keyup',e=>{if(typing(e))return;
  const id=KMAP[e.code];if(id&&KEY[id])KEY[id].up();});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopAll();});
async function jget(u){try{const r=await(await fetch(u)).json();
  if(!r.ok)alert('erro: '+(r.err||'?'));return r;}catch(e){alert('sem liga\\u00e7\\u00e3o');return {ok:false};}}
async function jpost(u,body){try{const r=await(await fetch(u,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(!r.ok)alert('erro: '+(r.err||'?'));return r;}catch(e){alert('sem liga\\u00e7\\u00e3o');return {ok:false};}}
function kvHtml(p){return '<div class="kv">'+Object.keys(p).sort().map(k=>
  '<label>'+(NM[k]||k)+'<input data-k="'+k+'" value="'+p[k]+'" inputmode="decimal"></label>').join('')+'</div>'+
  '<div class="prow"><button class="kvs">guardar valores</button></div>';}
function bindKv(el,save){el.querySelector('.kvs').onclick=()=>{
  const vals={};el.querySelectorAll('input[data-k]').forEach(i=>{
    const v=parseFloat(i.value);if(!isNaN(v))vals[i.dataset.k]=v;});
  save(vals);};}
function drawPoses(){
  $('pl').innerHTML=POSES.map(p=>{
    let h='<div class="li"><button data-p="'+p+'">&#9654;</button><b>'+p+'</b>'+
      '<button class="sm" data-e="'+p+'">'+(OPENP===p?'fechar':'editar')+'</button>'+
      '<button class="sm" data-d="'+p+'">&times;</button></div>';
    if(OPENP===p)h+='<div class="dt" id="pdt">&hellip;</div>';
    return h;}).join('')||'<p class="note">sem poses &mdash; p&otilde;e o bra&ccedil;o em posi&ccedil;&atilde;o e "guardar atual"</p>';
  $('pl').querySelectorAll('button[data-p]').forEach(b=>b.onclick=()=>jget('/app/larm/posego?name='+encodeURIComponent(b.dataset.p)).then(stPoll));
  $('pl').querySelectorAll('button[data-e]').forEach(b=>b.onclick=()=>{
    OPENP=OPENP===b.dataset.e?'':b.dataset.e;drawPoses();});
  $('pl').querySelectorAll('button[data-d]').forEach(b=>b.onclick=()=>{
    if(confirm('apagar pose "'+b.dataset.d+'"?')){if(OPENP===b.dataset.d)OPENP='';
      jget('/app/larm/posedel?name='+encodeURIComponent(b.dataset.d)).then(stPoll);}});
  if(OPENP)loadPdt();
  const v=$('asel').value;
  $('asel').innerHTML=POSES.map(p=>'<option>'+p+'</option>').join('');
  if(POSES.indexOf(v)>=0)$('asel').value=v;}
async function loadPdt(){try{
  const j=await(await fetch('/app/larm/poseget?name='+encodeURIComponent(OPENP))).json();
  const el=$('pdt');if(!j.ok||!el)return;
  el.innerHTML=kvHtml(j.pose);
  bindKv(el,vals=>jpost('/app/larm/posesave',{name:j.name,vals:vals}).then(r=>{if(r.ok){OPENP='';drawPoses();}}));}catch(_){}}
function drawSteps(){$('asteps').textContent=AS.length?
  AS.map(s=>s.p+' ('+s.d+'ms)').join(' \\u2192 '):'(sem passos)';}
function drawAnims(){
  $('al').innerHTML=ANIMS.map(a=>{
    let h='<div class="li"><button data-a="'+a+'">&#9654;</button><b>'+a+'</b>'+
      '<button class="sm" data-e="'+a+'">'+(OPENA===a?'fechar':'ver')+'</button>'+
      '<button class="sm" data-x="'+a+'">&times;</button></div>';
    if(OPENA===a)h+='<div class="dt" id="adt">&hellip;</div>';
    return h;}).join('')||'<p class="note">sem anima&ccedil;&otilde;es &mdash; cria uma em cima</p>';
  $('al').querySelectorAll('button[data-a]').forEach(b=>b.onclick=()=>jget('/app/larm/animgo?name='+encodeURIComponent(b.dataset.a)).then(stPoll));
  $('al').querySelectorAll('button[data-e]').forEach(b=>b.onclick=()=>{
    OPENA=OPENA===b.dataset.e?'':b.dataset.e;drawAnims();});
  $('al').querySelectorAll('button[data-x]').forEach(b=>b.onclick=()=>{
    if(confirm('apagar anima\\u00e7\\u00e3o "'+b.dataset.x+'"?')){if(OPENA===b.dataset.x)OPENA='';
      jget('/app/larm/animdel?name='+encodeURIComponent(b.dataset.x)).then(stPoll);}});
  if(OPENA)loadAdt();}
async function loadAdt(){try{
  const j=await(await fetch('/app/larm/animget?name='+encodeURIComponent(OPENA))).json();
  const el=$('adt');if(!j.ok||!el)return;
  el.innerHTML='<div class="mono" style="margin-top:0">'+
    j.steps.map(s=>s.p+' <span style="opacity:.55">('+s.d+'ms)</span>').join(' \\u2192 ')+'</div>';}catch(_){}}
function drawCp(est){const cp='agora: '+Object.keys(est).sort().map(k=>(NM[k]||k)+' <b>'+Math.round(est[k])+'</b>').join(' \\u00b7 ');
  $('cpos').innerHTML=cp;$('cpos2').innerHTML=cp;}
async function stPoll(){try{
  const j=await(await fetch('/app/larm/state')).json();
  HOMEP=j.home||{};
  drawCp(j.est);
  $('gst').style.display=(j.goto||j.anim)?'block':'none';
  $('gst').textContent=j.anim?'anima\\u00e7\\u00e3o a correr\\u2026 (STOP TUDO cancela)':'a ir para a pose\\u2026 (STOP TUDO cancela)';
  const s=JSON.stringify(j.poses);
  if(s!==JSON.stringify(POSES)){POSES=j.poses;drawPoses();}
  const a=JSON.stringify(j.anims||[]);
  if(a!==JSON.stringify(ANIMS)){ANIMS=j.anims||[];drawAnims();}
}catch(_){}}
$('ps').addEventListener('click',()=>{const n=$('pn').value.trim();if(!n)return;
  jget('/app/larm/posesave?name='+encodeURIComponent(n)).then(r=>{if(r.ok)$('pn').value='';stPoll();});});
$('hsync').addEventListener('click',()=>jget('/app/larm/homesync').then(stPoll));
$('hset').addEventListener('click',()=>{
  if(confirm('a pose HOME passa a ser a posi\\u00e7\\u00e3o ATUAL?'))jget('/app/larm/homeset').then(stPoll);});
$('hedit').addEventListener('click',()=>{const d=$('hdt');
  if(d.style.display!=='none'){d.style.display='none';return;}
  if(!Object.keys(HOMEP).length){alert('home ainda n\\u00e3o definido \\u2014 usa "home = aqui"');return;}
  d.style.display='block';
  d.innerHTML=kvHtml(HOMEP);
  bindKv(d,vals=>jpost('/app/larm/homesave',{vals:vals}).then(r=>{if(r.ok)d.style.display='none';stPoll();}));});
$('aadd').addEventListener('click',()=>{const p=$('asel').value;if(!p)return;
  if(AS.length>=20){alert('max 20 passos');return;}
  AS.push({p:p,d:Math.max(0,Math.min(10000,parseInt($('ad').value)||0))});drawSteps();});
$('aclr').addEventListener('click',()=>{AS=[];drawSteps();});
$('asave').addEventListener('click',()=>{const n=$('an').value.trim();
  if(!n||!AS.length)return;
  jpost('/app/larm/animsave',{name:n,steps:AS}).then(r=>{
    if(r.ok){$('an').value='';AS=[];drawSteps();}stPoll();});});
function tab(n){for(let i=0;i<3;i++){$('tb'+i).className=i===n?'on':'';$('tab'+i).className=i===n?'tab on':'tab';}
  localStorage.larmtab=n;}
[0,1,2].forEach(i=>$('tb'+i).addEventListener('click',()=>tab(i)));
tab(parseInt((location.hash||'#').slice(1)||(location.search.match(/t=(\\d)/)||[])[1]||localStorage.larmtab||'0')||0);
/* poll lento e SUSPENSO enquanto seguras um botao (as coords vem pelo WS;
   evita colisoes HTTP<->WS no loop do S3 = ping baixo durante o controlo) */
setInterval(()=>{if(!Object.keys(HB).length)stPoll();},2000);
load();wsConnect();stPoll();
</script></body></html>"""
