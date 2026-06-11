#!/usr/bin/env python3
"""
larm.py — cliente de host para o L-ONE.

Fala HTTP com o **S3** (o comando), que retransmite por ESP-NOW para o C3 no
braço. Útil para testar do PC sem browser e como base da Fase 2 (webcam + AI ->
ângulos). Corre no PC ou no Raspberry Pi.

Uso:
    python larm.py 192.168.1.79 move 1 90
    python larm.py 192.168.1.79 home
"""
import sys
import json
import urllib.request


class LArm:
    def __init__(self, host, port=80, timeout=3):
        # bate na app larm do launcher espMP3
        self.base = "http://%s:%d/app/larm" % (host, port)
        self.timeout = timeout

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            body = r.read().decode()
        try:
            return json.loads(body)
        except ValueError:
            return body

    def move(self, idx, angle):
        """Move um servo. Os limites mecânicos são aplicados no C3."""
        return self._get("/move?idx=%d&angle=%d" % (int(idx), int(angle)))

    def home(self):
        return self._get("/home")

    def pose(self, angles):
        """angles: {1:90, 2:120, 3:60} — move vários servos em sequência."""
        return [self.move(idx, ang) for idx, ang in angles.items()]


def _cli():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    host, cmd = sys.argv[1], sys.argv[2]
    arm = LArm(host)
    if cmd == "move" and len(sys.argv) == 5:
        print(arm.move(int(sys.argv[3]), int(sys.argv[4])))
    elif cmd == "home":
        print(arm.home())
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()
