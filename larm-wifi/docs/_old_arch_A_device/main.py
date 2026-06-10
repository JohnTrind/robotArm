# main.py — L-ONE WiFi control (Arquitetura A: ESP serve a UI)
#
# Sobe WiFi STA, abre socket HTTP, serve UI com sliders, e chama
# ServosController.set_angle() com limites de segurança por servo.
#
# Padrão socket cru em MicroPython (mesma abordagem do espApps/vicode).
# NÃO depende do rc_module binário — só de bbl/servos.py + machine.

import network
import socket
import time
import config
from bbl.servos import ServosController

servos = ServosController()


def wifi_connect():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        print("[wifi] a ligar a", config.WIFI_SSID)
        sta.connect(config.WIFI_SSID, config.WIFI_PASS)
        t0 = time.time()
        while not sta.isconnected():
            if time.time() - t0 > 20:
                print("[wifi] timeout")
                return None
            time.sleep(0.5)
    ip = sta.ifconfig()[0]
    print("[wifi] ligado, IP:", ip)
    return ip


def clamp_to_limits(idx, angle):
    """Garante que o ângulo fica dentro dos limites mecânicos do servo."""
    lo, hi = config.SERVO_LIMITS.get(idx, (0, 180))
    if angle < lo:
        return lo
    if angle > hi:
        return hi
    return angle


def go_home():
    for idx in config.ACTIVE_SERVOS:
        ang = clamp_to_limits(idx, config.SERVO_HOME.get(idx, 90))
        servos.set_angle(idx, ang)


def html_page(ip):
    """UI: um slider por servo ativo, dentro dos limites configurados."""
    rows = []
    for idx in config.ACTIVE_SERVOS:
        lo, hi = config.SERVO_LIMITS.get(idx, (0, 180))
        home = config.SERVO_HOME.get(idx, 90)
        name = config.SERVO_NAMES.get(idx, "Servo %d" % idx)
        rows.append("""
        <div class="servo">
          <label>{name} <span id="v{idx}">{home}</span>&deg;</label>
          <input type="range" min="{lo}" max="{hi}" value="{home}"
                 oninput="move({idx}, this.value)">
        </div>""".format(name=name, idx=idx, lo=lo, hi=hi, home=home))

    return """HTTP/1.0 200 OK\r
Content-Type: text/html\r
\r
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>L-ONE</title>
<style>
  body{{font-family:monospace;background:#0d0d0d;color:#0f0;margin:0;padding:1.2rem;}}
  h1{{font-size:1.1rem;border-bottom:1px solid #0f0;padding-bottom:.4rem;}}
  .servo{{margin:1.4rem 0;}}
  label{{display:block;margin-bottom:.4rem;}}
  input[type=range]{{width:100%;}}
  button{{background:#111;color:#0f0;border:1px solid #0f0;padding:.6rem 1rem;
         font-family:monospace;font-size:1rem;width:100%;margin-top:1rem;}}
  .ip{{opacity:.5;font-size:.8rem;}}
</style></head><body>
<h1>L-ONE // wifi control</h1>
{rows}
<button onclick="home()">HOME</button>
<p class="ip">{ip}</p>
<script>
function move(idx,val){{
  document.getElementById('v'+idx).textContent=val;
  fetch('/servo?idx='+idx+'&angle='+val);
}}
function home(){{ fetch('/home').then(()=>location.reload()); }}
</script></body></html>""".format(rows="".join(rows), ip=ip)


def parse_query(path):
    """Extrai params simples de '/servo?idx=1&angle=90'."""
    params = {}
    if "?" in path:
        _, qs = path.split("?", 1)
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
    return params


def handle(conn, ip):
    try:
        req = conn.recv(1024)
        if not req:
            return
        line = req.split(b"\r\n", 1)[0].decode()
        # ex: "GET /servo?idx=1&angle=90 HTTP/1.1"
        parts = line.split(" ")
        path = parts[1] if len(parts) > 1 else "/"

        if path.startswith("/servo"):
            p = parse_query(path)
            idx = int(p.get("idx", "0"))
            angle = int(p.get("angle", "90"))
            if idx in config.ACTIVE_SERVOS:
                safe = clamp_to_limits(idx, angle)
                servos.set_angle(idx, safe)
                print("[servo] %d -> %d (pedido %d)" % (idx, safe, angle))
            conn.send(b"HTTP/1.0 200 OK\r\n\r\nok")

        elif path.startswith("/home"):
            go_home()
            conn.send(b"HTTP/1.0 200 OK\r\n\r\nhome")

        else:
            conn.send(html_page(ip).encode())
    except Exception as e:
        print("[http] erro:", e)
    finally:
        conn.close()


def main():
    ip = wifi_connect()
    if ip is None:
        print("[main] sem WiFi, a abortar")
        return

    go_home()  # arranca numa pose segura conhecida

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", config.HTTP_PORT))
    s.listen(2)
    print("[main] HTTP em http://%s:%d" % (ip, config.HTTP_PORT))

    while True:
        try:
            conn, addr = s.accept()
            handle(conn, ip)
        except Exception as e:
            print("[main] loop erro:", e)


if __name__ == "__main__":
    main()
