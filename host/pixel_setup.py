#!/usr/bin/env python3
"""pixel_setup.py -- setup COMPLETO da camara do Pixel num comando. Corre do PC.

Automatiza a cadeia toda (o pixel_cam.py so fazia o relay+forward; isto faz
tambem a sessao Waydroid e a app, com verificacao passo a passo):

  1. adb ve o telemovel?
  2. sessao Waydroid a correr? (se nao: arranca-a com as env vars certas e espera)
  3. app "Android IP Camera" em FOREGROUND? (relanca; se ficar atras, o stream para)
  4. relay python no telemovel (127.0.0.1:4445 -> 192.168.240.112:4444)
  5. adb forward PC:4444 -> telemovel:4445
  6. testa https://localhost:4444/stream (com retries -- a camara demora a servir)

Uso (Pixel por USB, depuracao USB ON):
  python pixel_setup.py            # setup completo + teste
  python pixel_setup.py --status   # so diagnostico de cada elo, nao mexe em nada
  python pixel_setup.py --stop     # desliga relay + forward (Waydroid fica)

Se o passo 6 falhar com SSL EOF / frames travados, o HAL da camara encravou:
reinicia a sessao (corre isto outra vez com --restart-session) e, em ultimo
caso, reinicia o telemovel. Detalhe: pixel_camera.md.
"""
import argparse
import subprocess
import ssl
import sys
import time
import urllib.request
import urllib.error

from pixel_cam import relay_src, RELAY_PORT, PC_PORT, RELAY_REMOTE, stop as relay_stop

APP_ID = "com.github.digitallyrefined.androidipcamera"
# env que TODOS os comandos waydroid precisam no host Ubuntu Touch (sem isto:
# "Unable to autolaunch a dbus-daemon without a $DISPLAY")
WD_ENV = ("XDG_RUNTIME_DIR=/run/user/32011 "
          "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/32011/bus "
          "WAYLAND_DISPLAY=wayland-0")
STREAM_URL = "https://localhost:%d/stream" % PC_PORT


def adb(*args, timeout=30):
    try:
        return subprocess.run(["adb", *args], capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        class T:
            returncode, stdout, stderr = 124, "", "timeout"
        return T()


def sh(cmd, timeout=30):
    """comando no host UT do telemovel."""
    return adb("shell", cmd, timeout=timeout)


def wd(cmd, timeout=30):
    """comando waydroid com as env vars certas."""
    return sh("export %s; waydroid %s" % (WD_ENV, cmd), timeout=timeout)


def step(n, msg):
    print("[%d] %s" % (n, msg), end=" ", flush=True)


def ok(extra=""):
    print("OK" + ((" -- " + extra) if extra else ""))


def fail(why, hint=""):
    print("FALHOU -- " + why)
    if hint:
        print("    -> " + hint)
    sys.exit(1)


# --- elos da cadeia ---
def check_adb():
    out = adb("devices").stdout
    devs = [l for l in out.splitlines()[1:] if l.strip().endswith("device")]
    return devs


def session_running():
    return "RUNNING" in wd("status").stdout


def start_session(wait_s=90):
    # destacada, senao o adb shell fica pendurado para sempre
    sh("(setsid env %s waydroid session start >/tmp/wd.log 2>&1 </dev/null &)"
       % WD_ENV)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if session_running():
            return True
        time.sleep(3)
    return False


def stop_session():
    wd("session stop", timeout=60)


def app_foreground():
    """best-effort: o dumpsys vive DENTRO do container Android."""
    r = sh("export %s; sudo waydroid shell -- sh -c "
           "'dumpsys activity activities | grep topResumed'" % WD_ENV)
    out = (r.stdout or "") + (r.stderr or "")
    if "topResumed" not in out:
        return None          # nao se conseguiu ver (sudo/etc.) -> inconclusivo
    return APP_ID in out


def launch_app():
    wd("app launch %s" % APP_ID, timeout=60)


def force_stop_app():
    """mata a app DENTRO do container (larga o cliente zombie do /stream).
    sudo -n: sem password nao pendura; se falhar, o relaunch ainda ajuda."""
    sh("export %s; sudo -n waydroid shell -- am force-stop %s 2>/dev/null"
       % (WD_ENV, APP_ID), timeout=30)


def start_relay():
    p = subprocess.run(["adb", "shell", "cat > %s" % RELAY_REMOTE],
                       input=relay_src(), text=True, capture_output=True)
    if p.returncode != 0:
        return False
    sh("pkill -f '[r]elay.py'")
    sh("(setsid python3 -u %s >/home/phablet/relay.log 2>&1 </dev/null &)"
       % RELAY_REMOTE)
    time.sleep(1)
    return "relay up" in sh("cat /home/phablet/relay.log").stdout


def forward():
    adb("forward", "--remove", "tcp:%d" % PC_PORT)
    return adb("forward", "tcp:%d" % PC_PORT, "tcp:%d" % RELAY_PORT).returncode == 0


def probe_stream(timeout=8):
    """-> (estado, detalhe). estado: 'ok' | 'auth' | 'busy' | 'down'"""
    ctx = ssl._create_unverified_context()
    try:
        r = urllib.request.urlopen(STREAM_URL, timeout=timeout, context=ctx)
        ct = r.headers.get("Content-Type", "")
        if "multipart" in ct:
            r.read(2048)     # confirma que vem mesmo um frame, nao so headers
            return "ok", ct
        return "down", "responde mas nao e MJPEG (%s)" % ct
    except urllib.error.HTTPError as e:
        body = e.read()[:120].decode("utf-8", "replace")
        if e.code == 403:
            return "auth", body
        if e.code == 503:
            return "busy", body
        return "down", "HTTP %d: %s" % (e.code, body)
    except Exception as e:
        return "down", str(e)


def status():
    print("== estado da cadeia ==")
    devs = check_adb()
    print("adb:      %s" % (devs[0].split()[0] if devs else "SEM telemovel"))
    if not devs:
        return
    print("waydroid: %s" % ("RUNNING" if session_running() else "parado"))
    fg = app_foreground()
    print("app:      %s" % ("foreground" if fg else
                            "NAO esta a frente" if fg is False else
                            "(nao consegui verificar)"))
    print("relay:    %s" % ("a correr" if sh(
        "pgrep -f '[r]elay.py'").stdout.strip() else "parado"))
    fwd = adb("forward", "--list").stdout
    print("forward:  %s" % ("tcp:%d ok" % PC_PORT if
                            ("tcp:%d" % PC_PORT) in fwd else "nao esta"))
    st, det = probe_stream(timeout=5)
    print("stream:   %s (%s)" % (st, det))


def setup(restart_session=False):
    step(1, "adb ve o telemovel?")
    devs = check_adb()
    if not devs:
        fail("nenhum device", "cabo USB + depuracao USB ON + autorizar o PC")
    ok(devs[0].split()[0])

    if restart_session:
        step(2, "a reiniciar a sessao Waydroid (pedido)...")
        stop_session()
        if not start_session():
            fail("sessao nao arrancou", "ver /tmp/wd.log no telemovel")
        ok()
    else:
        step(2, "sessao Waydroid?")
        if session_running():
            ok("ja estava RUNNING")
        else:
            print("parada, a arrancar (pode demorar ~1 min)...", end=" ",
                  flush=True)
            if not start_session():
                fail("nao chegou a RUNNING", "ver /tmp/wd.log; ou reinicia o telemovel")
            ok()

    step(3, "app IP Camera em foreground?")
    launch_app()
    time.sleep(3)
    fg = app_foreground()
    ok("confirmado" if fg else "lancada (foreground nao verificavel daqui)")

    step(4, "relay no telemovel?")
    if not start_relay():
        fail("relay nao arrancou", "adb shell cat /home/phablet/relay.log")
    ok("127.0.0.1:%d -> camara interna" % RELAY_PORT)

    step(5, "adb forward?")
    if not forward():
        fail("adb forward recusou")
    ok("PC:%d -> telemovel:%d" % (PC_PORT, RELAY_PORT))

    step(6, "stream a dar frames?")
    # arranque frio (reboot/sessao nova): a app demora BEM mais de 30s a abrir
    # a camara -- janela total ~90s, com re-foreground da app a meio.
    last = ""
    for i in range(14):
        st, det = probe_stream()
        if st == "ok":
            ok(det)
            print("\nPRONTO. No PC:")
            print("  python vision.py            # preview")
            print("  python vision.py --point    # apontar ao alvo")
            return
        if st == "auth":
            fail("403: " + det,
                 "abre a app no telemovel e poe username+password validos")
        if st == "busy":                   # ligacao zombie de outro cliente
            if i >= 1:                     # persistiu: force-stop + relancar app
                print("(busy: force-stop da app...)", end=" ", flush=True)
                force_stop_app()
                time.sleep(2)
                launch_app()
                time.sleep(4)
            sh("pkill -f '[r]elay.py'")
            start_relay()
        elif i in (4, 9):                  # 'down' persistente: nudge ao foreground
            print("(ainda nada, a re-lancar a app...)", end=" ", flush=True)
            launch_app()
        last = "%s: %s" % (st, det)
        time.sleep(6)
    fail(last, "HAL encravado? tenta --restart-session; ultimo recurso: "
               "reiniciar o telemovel (pixel_camera.md)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="setup da camara do Pixel num comando")
    ap.add_argument("--status", action="store_true", help="so diagnostico")
    ap.add_argument("--stop", action="store_true", help="desliga relay+forward")
    ap.add_argument("--restart-session", action="store_true",
                    help="reinicia a sessao Waydroid antes (HAL encravado)")
    a = ap.parse_args()
    if a.stop:
        relay_stop()
    elif a.status:
        status()
    else:
        setup(restart_session=a.restart_session)
