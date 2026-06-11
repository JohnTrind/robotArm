"""Espera o C3 aparecer por USB, le o MAC (para unicast) e reinicia o recetor.
Uso: python c3_mac.py
"""
import time
import sys
from serial.tools import list_ports
import c3_raw


def find_c3():
    for p in list_ports.comports():
        if p.vid == 0x303A and p.pid == 0x1001:
            return p.device
    return None


print("a aguardar o C3 por USB (liga o cabo de dados do braco ao PC)...")
port = None
for _ in range(900):  # ate 3 min
    port = find_c3()
    if port:
        break
    time.sleep(0.2)
if not port:
    print("FAIL: C3 nao apareceu.")
    sys.exit(1)

print("C3 em", port, "- a ler MAC...")
s = c3_raw.connect(port)
out, err = c3_raw.exec_raw(s, (
    "import network, ubinascii\n"
    "w=network.WLAN(network.STA_IF); w.active(True)\n"
    "print('MAC', ubinascii.hexlify(w.config('mac')).decode())\n"))
print(out)
if err.strip():
    print("erro:", err)
# reinicia o recetor (Ctrl-D soft reset) para voltar a ouvir ESP-NOW
try:
    s.write(b"\x02")
    time.sleep(0.2)
    s.write(b"\x04")
    time.sleep(0.5)
except Exception:
    pass
s.close()
print("C3 reiniciado (recetor a correr).")
