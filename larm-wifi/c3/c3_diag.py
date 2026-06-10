"""Apanha o C3 na janela de arranque (2s antes do WiFi), fica no REPL com USB
vivo, e testa os 3 servos diretamente (isola hardware do ESP-NOW).
Uso: python c3_diag.py [COM18]
"""
import sys
import time
import serial
from serial.tools import list_ports
import c3_raw


def find_c3():
    # C3 = Espressif USB-Serial-JTAG (VID 303A, PID 1001). O S3 e' PID 4001.
    for p in list_ports.comports():
        if p.vid == 0x303A and p.pid == 0x1001:
            return p.device
    return None


port = sys.argv[1] if len(sys.argv) > 1 else None

print("a aguardar que o C3 (303A:1001) apareca (liga/religa a corrente do braco)...")
ports_seen = set(p.device for p in list_ports.comports())
print("portas agora:", sorted(ports_seen))
port = None
for _ in range(900):  # ate 3 min
    port = find_c3()
    if port:
        break
    time.sleep(0.2)
if not port:
    print("FAIL: C3 (303A:1001) nunca apareceu. Confirma cabo USB de DADOS + corrente.")
    print("portas finais:", sorted(p.device for p in list_ports.comports()))
    sys.exit(1)

print("C3 apareceu em", port, "! a entrar no REPL antes do WiFi...")
s = c3_raw.connect(port)   # spam Ctrl-C (apanha o sleep(2) do boot) + raw REPL

TEST = """import machine
print('reset_cause', machine.reset_cause())
from bbl.servos import ServosController
import time
sv=ServosController()
for idx in (1,2,3):
    sv.set_angle(idx,70); print('servo',idx,'-> 70 (duty)')
    time.sleep(0.5)
    sv.set_angle(idx,110); print('servo',idx,'-> 110')
    time.sleep(0.5)
print('SERVO_TEST_DONE')
"""
out, err = c3_raw.exec_raw(s, TEST)
print("--- saida ---")
print(out)
if err.strip():
    print("--- erro ---")
    print(err)
s.write(b"\x02")
s.close()
