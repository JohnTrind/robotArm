# config.py — configuração do L-ONE WiFi control
# NÃO commitar credenciais reais. Edita localmente antes do deploy.

# --- WiFi (modo STA: liga-se à tua rede de casa) ---
WIFI_SSID = "A_TUA_REDE"
WIFI_PASS = "A_TUA_PASS"

# Porta do servidor HTTP
HTTP_PORT = 80

# --- Servos ---
# Quais os servo_idx (1..4) realmente usados pelo L-ONE.
# CONFIRMAR no REPL: ServosController().set_angle(idx, 90) e ver qual mexe.
ACTIVE_SERVOS = [1, 2, 3]

# Nomes amigáveis para a UI (ajusta à anatomia real do teu braço)
SERVO_NAMES = {
    1: "Base (yaw)",
    2: "Junta 1",
    3: "Junta 2",
    4: "Servo 4",
}

# LIMITES MECÂNICOS por servo (graus). Conservadores de início!
# Afina com o braço à frente, mexendo DEVAGAR, antes de alargar.
# Mandar fora destes limites é bloqueado no firmware -> protege hardware.
SERVO_LIMITS = {
    1: (30, 150),
    2: (30, 150),
    3: (30, 150),
    4: (30, 150),
}

# Ângulo seguro de arranque ("home") para cada servo
SERVO_HOME = {
    1: 90,
    2: 90,
    3: 90,
    4: 90,
}
