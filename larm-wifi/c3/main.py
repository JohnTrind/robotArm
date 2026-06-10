# main.py — C3 recetor ESP-NOW (braço CyberBrick L-ONE 2.0).
# (No device vive como /larm_recv.py, importado pelo nosso /boot.py.)
#
# Atuadores do L-ONE 2.0 (do rc_config oficial):
#   servos idx1 (PWM1) = rotação contínua (junta)   -> set_angle (duty = velocidade)
#   servos idx2 (PWM2) = rotação contínua (junta)   -> set_angle
#   servos idx3 (PWM3) = posicional = CLAW          -> set_angle (ângulo)
#   MOTOR1 (DC, GPIO 4/5)  = base/yaw               -> MotorsController.set_speed
#
# Protocolo (prefixo b"LA"):
#   servo  : b"LA" + b"M" + bytes([idx, angle])             (idx 1..4, ang 0..180)
#   motor  : b"LA" + b"D" + bytes([motor_idx, speed+100])   (speed -100..100)
#   home   : b"LA" + b"H"
#   beacon : b"LA" + b"B" + bytes([canal])  (S3 emite ~1-4Hz com o canal REAL)
#
# CANAL AUTOMÁTICO: o C3 começa no último canal que funcionou (/larm_ch.txt,
# fallback config.ESPNOW_CHANNEL). Se não ouvir nada, faz SCAN 1..13 até
# apanhar um pacote "LA*". Os canais WiFi sobrepõem-se (ouve-se 1-2 canais ao
# lado), por isso o beacon DECLARA o canal verdadeiro do S3 e o C3 tuna para
# lá e persiste-o. Com lock, 120s de silêncio -> volta ao scan (cobre router
# a mudar de canal). Nunca mais se reconfigura à mão.
#
# FAILSAFE (watchdog local): o S3 faz stream ~30Hz enquanto um atuador está
# ativo. Se um servo CONTÍNUO estiver fora do neutro, ou o motor a rodar, e
# passarem >FAILSAFE_MS sem QUALQUER pacote -> volta a neutro/0 sozinho.
# Garante que pacote de STOP perdido (ou S3 morto) nunca deixa o braço a rodar.
# A claw (posicional) NÃO é tocada — segura a posição.
#
# SEM WiFi, SEM pairing. Só espnow + bbl (Python aberto). NÃO toca no rc_module.

import time

import network
import espnow
import config
from bbl.servos import ServosController

MAGIC = b"LA"
CH_FILE = "/larm_ch.txt"
SCAN_DWELL_MS = 1100    # ouvir por canal no scan (beacon em idle pode ser ~1Hz)
RESCAN_MS = 120000      # silêncio com lock antes de voltar ao scan

servos = ServosController()
_motors = None


def motors():
    """Lazy: só inicia o driver DC (easypwm em GPIO 4/5/6/7) se for preciso."""
    global _motors
    if _motors is None:
        from bbl.motors import MotorsController
        _motors = MotorsController()
    return _motors


def clamp(idx, angle):
    lo, hi = config.SERVO_LIMITS.get(idx, (0, 180))
    if angle < lo:
        return lo
    if angle > hi:
        return hi
    return angle


def go_home():
    for idx in config.ACTIVE_SERVOS:
        servos.set_angle(idx, clamp(idx, config.SERVO_HOME.get(idx, 90)))


def motor_speed(midx, pct):
    """pct -100..100 -> set_speed -2048..2048, com tecto de segurança."""
    cap = config.MOTOR_MAX_PCT
    if pct > cap:
        pct = cap
    if pct < -cap:
        pct = -cap
    motors().set_speed(midx, int(pct / 100.0 * 2048))


def load_ch():
    try:
        with open(CH_FILE) as f:
            ch = int(f.read().strip())
        if 1 <= ch <= 13:
            return ch
    except Exception:
        pass
    return None


def save_ch(ch):
    try:
        with open(CH_FILE, "w") as f:
            f.write(str(ch))
    except Exception as e:
        print("[c3] nao guardei canal:", e)


def main():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)

    def set_ch(ch):
        try:
            sta.config(channel=ch)
            return True
        except Exception as ex:
            print("[c3] canal", ch, "falhou:", ex)
            return False

    e = espnow.ESPNow()
    e.active(True)

    cont = getattr(config, "CONT_SERVOS", [])
    fs_ms = getattr(config, "FAILSAFE_MS", 600)
    moving = {}            # ("s", idx) ou ("m", midx) -> True se fora do neutro

    saved = load_ch()
    start = saved or config.ESPNOW_CHANNEL
    chans = [start] + [c for c in range(1, 14) if c != start]
    ci = 0
    ch = chans[0]
    set_ch(ch)
    locked = False
    now = time.ticks_ms()
    last_rx = now
    dwell_t0 = now

    print("[c3] recetor pronto: canal", ch, "(a confirmar por beacon),",
          "failsafe", fs_ms, "ms")

    while True:
        try:
            _, msg = e.recv(200)   # timeout p/ watchdog + scan correrem
            now = time.ticks_ms()

            if msg and msg.startswith(MAGIC):
                last_rx = now
                cmd = msg[2:3]
                # Beacon declara o canal REAL do S3 (os canais WiFi sobrepoem-se,
                # por isso da para ouvir 1-2 canais ao lado — tunar ao certo):
                if cmd == b"B" and len(msg) >= 4 and 1 <= msg[3] <= 13 \
                        and msg[3] != ch:
                    ch = msg[3]
                    set_ch(ch)
                    print("[c3] beacon: canal real e", ch)
                if not locked:
                    locked = True
                    print("[c3] canal confirmado:", ch)
                if ch != saved:
                    save_ch(ch)
                    saved = ch
                if cmd == b"M" and len(msg) >= 5:
                    idx, angle = msg[3], msg[4]
                    if idx in config.ACTIVE_SERVOS:
                        angle = clamp(idx, angle)
                        servos.set_angle(idx, angle)
                        if idx in cont:
                            neutral = config.SERVO_HOME.get(idx, 90)
                            moving[("s", idx)] = (angle != neutral)
                elif cmd == b"D" and len(msg) >= 5:
                    midx, pct = msg[3], msg[4] - 100
                    if midx in config.MOTORS:
                        motor_speed(midx, pct)
                        moving[("m", midx)] = (pct != 0)
                elif cmd == b"H":
                    go_home()
                    for m in config.MOTORS:
                        try:
                            motors().set_speed(m, 0)
                        except Exception:
                            pass
                    moving.clear()
                continue

            # --- sem pacote (timeout do recv) ---
            # 1) failsafe: algo a rodar + silêncio acima do limite -> parar
            if time.ticks_diff(now, last_rx) > fs_ms:
                for key in list(moving.keys()):
                    if moving[key]:
                        kind, i = key
                        if kind == "s":
                            servos.set_angle(i, config.SERVO_HOME.get(i, 90))
                        else:
                            motor_speed(i, 0)
                        print("[c3] FAILSAFE: parei", kind, i)
                    del moving[key]
            # 2) canal: sem lock -> avança o scan; com lock mas silêncio longo
            #    (router mudou de canal?) -> perde o lock e volta a procurar
            if locked:
                if time.ticks_diff(now, last_rx) > RESCAN_MS:
                    locked = False
                    dwell_t0 = now
                    print("[c3] silencio longo: scan de canal...")
            elif time.ticks_diff(now, dwell_t0) > SCAN_DWELL_MS:
                ci = (ci + 1) % len(chans)
                ch = chans[ci]
                set_ch(ch)
                dwell_t0 = now
        except Exception as ex:
            print("[c3] loop erro:", ex)
            time.sleep_ms(50)


main()
