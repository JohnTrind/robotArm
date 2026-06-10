# boot.py — L-ONE receiver (robotArm).
#
# SUBSTITUI o boot.py oficial do CyberBrick (que corria /app/rc_main.py).
# Original preservado em /boot_orig.py (device) e no backup do host.
# Para reverter: copiar boot_orig.py -> boot.py e reset.
#
# A pausa de 2s ANTES de ativar o WiFi dá sempre uma janela para entrar no
# REPL com Ctrl-C (ativar o rádio re-inicializa o USB-Serial-JTAG).
import time
print("[boot] L-ONE receiver: 2s para Ctrl-C antes do WiFi...")
time.sleep(2)
try:
    import larm_recv  # corre larm_recv.main() (loop ESP-NOW)
except Exception as e:
    import sys
    sys.print_exception(e)
