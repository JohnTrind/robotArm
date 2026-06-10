# config.py — C3 (recetor no braço L-ONE 2.0).
# O C3 NÃO sabe WiFi nenhuma: só ouve ESP-NOW do S3 e mexe atuadores.
# Os limites mecânicos vivem AQUI — é o último guarda antes do hardware.

# Canal ESP-NOW inicial (só uma DICA: o recetor faz scan 1..13 guiado pelo
# beacon do S3 e persiste o canal que funcionar em /larm_ch.txt).
ESPNOW_CHANNEL = 1

# Mapa do L-ONE 2.0 (do rc_config oficial):
#   idx1 (PWM1) = servo rotação contínua (junta)
#   idx2 (PWM2) = servo rotação contínua (junta)
#   idx3 (PWM3) = servo posicional = CLAW
#   MOTOR1      = motor DC (base/yaw)
ACTIVE_SERVOS = [1, 2, 3]

# Servos de rotação CONTÍNUA (sem posição): cobertos pelo failsafe — voltam
# ao neutro (SERVO_HOME) se o sinal do S3 desaparecer. A claw fica de fora
# (posicional: segura a posição, nunca é mexida pelo watchdog).
CONT_SERVOS = [1, 2]

# Failsafe: ms de silêncio rádio com um contínuo/motor ativo antes de parar
# tudo. O S3 faz stream ~30Hz quando algo mexe -> 600ms = ~18 pacotes perdidos.
FAILSAFE_MS = 600

# Motores DC ativos (MOTOR1 = base). MotorsController usa GPIO 4/5.
MOTORS = [1]
# Tecto de velocidade do motor (%) — começa conservador, sobe quando confiares.
MOTOR_MAX_PCT = 60

# LIMITES por idx (graus). Para servos CONTÍNUOS (1,2) isto é faixa de
# velocidade (30=rápido p/ um lado, 150=rápido p/ o outro, ~92=parado).
# Para a CLAW (3) é ângulo real; alarga aqui se precisares de abrir/fechar mais.
SERVO_LIMITS = {
    1: (30, 150),
    2: (30, 150),
    3: (30, 150),
}

# "home": contínuos -> ponto NEUTRO (paragem, ~92); claw -> pose de arranque.
SERVO_HOME = {
    1: 92,
    2: 92,
    3: 90,
}
