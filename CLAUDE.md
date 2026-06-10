# CLAUDE.md — L-ONE WiFi Control

Contexto para o Claude Code continuar este projeto. Lê isto primeiro.

## O que é

Controlo **wireless** de um braço robótico **CyberBrick L-ONE** (kit Bambu Lab),
substituindo o controlo por remote/app oficial por um servidor HTTP servido pelo
próprio recetor. Objetivo de longo prazo: webcam + AI a gerar ângulos para o braço.

O dono (Trinity) é QA automation engineer, forte em ESP32/MicroPython/WiFi
(ver repos `JohnTrind/espApps`, padrão `vicode`/`localchat` — ESP serve UI web na LAN).

## Arquitetura escolhida: "S3-remote" (o S3 faz de comando)

```
[browser telemóvel/PC] --HTTP/WiFi--> [ESP32-S3 = remote] --ESP-NOW broadcast--> [ESP32-C3 no braço] --PWM--> servos
```

Decisão (sessão 2026-06-09): em vez do C3 servir tudo (era a "Arquitetura A",
agora arquivada em `larm-wifi/docs/_old_arch_A_device/`), o **ESP32-S3** (a placa
do `espMP3`) faz de comando:
- O S3 serve a UI com sliders como uma **app do launcher espMP3** (`apps/larm.py`,
  rota `/app/larm/`). Pode estar em STA (rede de casa) **ou** em AP mode.
- Cada movimento vai por **ESP-NOW broadcast** (`FF:FF:FF:FF:FF:FF`) para o C3.
- O **C3 fica "burro"**: só ouve ESP-NOW, faz clamp aos limites e mexe servos.

**Porquê:** ESP-NOW é peer-to-peer, não precisa de router. Logo **o braço (C3)
nunca precisa de saber WiFi nenhuma** — nunca o reconfiguras para redes novas.
Configuras só o S3. Broadcast = **sem pairing**, sem precisar do MAC do C3.

Fase 2 (futura): o "cérebro" (webcam + AI) no Raspberry Pi fala HTTP com o S3
(`host/larm.py` já aponta lá) e o S3 retransmite por ESP-NOW. Não está nesta v1.

## Hardware — FACTOS CONFIRMADOS (do código oficial)

- **Comando = ESP32-S3** (placa `espMP3`, Generic ESP32S3 + Octal-SPIRAM,
  MicroPython 1.28, ~8 MB RAM livre). MAC STA `1c:db:d4:79:2b:30`. No PC do Trinity
  aparece em **COM17**. Tem `espnow` OK. Corre o launcher espMP3 (frog, etc.) +
  a nossa app `larm`. **NOTA:** COM17 é o S3, NÃO o braço — não confundir.
- **Recetor = ESP32-C3** com WiFi. Fica montado no disco que roda (yaw) na base.
  É por isso que USB tethered NÃO serve: o cabo torceria com a rotação. (Já não
  precisa de WiFi no nosso desenho — só ESP-NOW.)
- **Canal ESP-NOW: AUTOMÁTICO desde 2026-06-10.** S3 e C3 têm de estar no mesmo
  canal; em STA o canal do S3 = o do router (que MUDA sozinho — já esteve em 1,
  está em 8). Solução: o S3 emite um **beacon** `b"LA"+b"B"+bytes([canal])`
  (~4Hz, broadcast, dentro do `_stream`); o C3 arranca no último canal que
  funcionou (`/larm_ch.txt`), faz scan 1..13 se não ouvir nada, e **tuna para o
  canal declarado no beacon** (os canais WiFi sobrepõem-se — ouve-se 1-2 canais
  ao lado, por isso só a contagem/RSSI não chega para desambiguar; o canal no
  payload resolve). 120s de silêncio com lock -> volta ao scan. **Nunca mais se
  configura canal à mão.** `ESPNOW_CHANNEL` no `c3/config.py` é só a dica inicial.
- **Servos via PWM**, da classe `bbl/servos.py` (incluída, 100% Python aberto, só
  depende de `machine.Pin/PWM` — NÃO depende do `rc_module` binário):
  - `servo_idx 1` -> GPIO **3**
  - `servo_idx 2` -> GPIO **2**
  - `servo_idx 3` -> GPIO **1**
  - `servo_idx 4` -> GPIO **0**
  - freq 50 Hz; `duty = angle*102/180 + 25` (0°->25, 180°->127)
- **MAPA REAL do L-ONE 2.0** (decifrado do `/rc_config` oficial — ver
  `c3/backup_cyberbrick/rc_config`):
  - **idx1 (PWM1)** = servo **rotação CONTÍNUA** (junta) — tipo "speed". set_angle
    funciona como velocidade: ~92 (duty 77) = parado; 30/150 = roda p/ cada lado.
  - **idx2 (PWM2)** = servo **rotação CONTÍNUA** (junta) — igual.
  - **idx3 (PWM3)** = servo **posicional = CLAW** — tipo "angle" (0..180, usável
    ~0..120). É o único que respondia antes. **Confirmado pelo Trinity.**
  - **MOTOR1** = **motor DC** (base/yaw), GPIO **4/5** via `bbl/motors.py`
    (`easypwm`, `set_speed(-2048..2048)`). NÃO é servo — precisa do recetor C3 com
    suporte a motor (já em `c3/main.py`, falta deploy por cabo).
  - **PWM4 / idx4 / MOTOR2 = NÃO usados** no L-ONE 2.0.
  - Porque é que idx1/idx2 "não faziam nada": são contínuos; os botões +/− mexiam
    só ~2° à volta do neutro. Como "cont" (◀/▶) já rodam.

## Bloqueador resolvido + restrições do firmware

- O firmware CyberBrick é **restrito**: NÃO se pode reflashar com esptool/thonny
  (firmware fica permanentemente danificado). O WebREPL e acesso low-level a flash/BT
  foram desativados. **Não reflashar. Só editar ficheiros no filesystem (fluxo suportado).**
- O transport ESP-NOW remote<->recetor está num módulo binário (`rc_module`,
  `cyberbrick`) que NÃO está no repo. Por isso imitar o remote em ESP-NOW = reverse
  engineering. **Evitado** ao usar WiFi+socket diretamente.
- **CONFIRMADO na doc oficial** que `network` (WLAN STA/AP), `socket` e `espnow`
  existem no firmware. **A VALIDAR na unidade real** (ver Pré-requisito abaixo).
- Os servos (`bbl/servos.py`) são independentes do binário -> controlo direto OK.

## PRÉ-REQUISITO — validar no C3 antes de assumir que a v1 funciona

S3 já validado (espnow OK, app a servir, broadcasts a sair). Falta o C3.
No REPL do C3 (USB-C, 115200 baud):
```python
import network, espnow
sta = network.WLAN(network.STA_IF); sta.active(True)
sta.config(channel=1)          # tem de aceitar
e = espnow.ESPNow(); e.active(True)   # tem de dar sem erro
```
Se `espnow` rebentar ou `config(channel=...)` falhar, repensar (ex.: fixar canal
pondo o S3 em AP mode).

## ANTES DE MEXER — backup obrigatório

Substituir o `boot.py` deles apaga o mundo da app oficial + pairing com o remote.
Fazer backup do filesystem do recetor primeiro:
```bash
mpremote connect PORT fs cp -r : ./backup_cyberbrick/
```
(PORT = /dev/ttyACM0 no Linux/Pi, COMx no Windows)

Ficheiros originais a preservar: `boot.py`, `rc_main` (+ `app/`, `bbl/`), `rc_config`.
Trade-off conhecido: com o nosso boot, **perde-se controlo pelo remote oficial**
(o pairing está no binário). Para o objetivo AI isto é irrelevante.

## Estado atual — V1 FECHADA (sessão 2026-06-10)

Review completo + estabilização. Os 3 bugs que causavam "continua a ação até
mandares contrária / não responsive" foram encontrados e corrigidos:

1. **Tipo "jog" errado nas juntas (S3)** — idx1/2 estavam como "jog" (posicional
   que SEGURA o último valor ao largar). Mas são servos CONTÍNUOS (valor =
   velocidade) -> largar o botão continuava a rodar para sempre. Voltaram a
   "cont": largar -> setpoint = neutro -> pára. O tipo "jog" foi REMOVIDO.
2. **Calibração corrompia o neutro (S3)** — `larm_cal.json` tinha home=80/85
   nas juntas (das experiências posicionais); o neutro real é ~92. STOP enviava
   80 -> junta rodava devagar. Corrigido (home=92, garra mantém 30..110).
3. **Canais ESP-NOW diferentes** — o router mudou de canal 1 -> 8; o C3 estava
   fixo no 1 -> pacotes nem chegavam. Agora: beacon com canal + autoscan (ver
   Hardware). Self-healing.

Arquitetura de segurança em camadas (nada fica a rodar sozinho):
- Browser manda INTENÇÃO via WS (premir/largar + heartbeat 300ms); setpoint no
  S3 tem EXPIRY 500ms -> sem heartbeat, volta a neutro.
- `_stream()` (30Hz) é o ÚNICO caminho p/ movimento contínuo — WS, HTTP e
  sequências passam todos por setpoint+expiry. HTTP é time-boxed (default
  1500ms, cap 3000ms). WS desligou-se -> `_stop_all()` imediato.
- **C3 com watchdog próprio (FAILSAFE_MS=600)**: contínuo fora do neutro ou
  motor a rodar + 600ms sem QUALQUER pacote -> neutro/0 sozinho. Cobre pacote
  de STOP perdido e S3 morto. A garra (posicional) nunca é tocada pelo watchdog.
- **UNICAST**: `C3_MAC = 80:f1:b2:1e:13:6c` preenchido (retries MAC-layer).
  Broadcast só para o beacon. `C3_MAC=None` volta a broadcast.

- [x] S3: stream + beacon + WS:81 arrancam no BOOT (main.py chama `_ensure_ws()`
      no `_startup`), não só na 1ª visita à UI.
- [x] UI nova (1 ecrã): Base/Cotovelo/Ombro = ◀/■/▶ hold-to-move com feedback
      visual (botão acende), garra = fechar/abrir + slider + valor, seletor de
      velocidade lento/médio/rápido (persiste em localStorage), STOP TUDO +
      HOME, pílula de estado (WS/canal/RTT), teclado no PC (←/→ q/a w/s e/d,
      espaço=stop), auto-stop ao esconder o separador. Fallback HTTP se WS cair.
- [x] WS protocolo: "v,i,dir[,spd]" junta | "b,m,dir[,spd]" base | "p,i,ang"
      garra | "x,i" stop junta | "s" stop tudo | "h" home. ("sp"/"bm"/"d"
      antigos removidos.)
- [x] Verificado: S3 boot limpo, C3 lock de canal (pior caso: gravado=1, scan
      -> beacon -> 8), `_stream_test.py` = 1 intenção -> ~20 envios -> pára
      sozinho, `recv(timeout)` + watchdog OK no C3 real.
- [x] HTTP API (Postman/fase AI): /move /motor /stop /stopall /home /config
      /save /seq /seqstop. Tudo time-boxed. Coleção: host/larm.postman_collection.json.
- [x] Sequências: POST /seq {steps:[{a,i,v,d}]} — a=p garra, c=junta (v=vel
      durante d ms), m=motor, h=home. Corre pelo stream (failsafe incluído).

### Por confirmar fisicamente (precisa bateria ligada)
- [ ] Largar botão pára MESMO a junta (neutro 92 pode precisar de afinação fina
      por servo — se uma junta "zumbir" parada, ajustar home via /save).
- [ ] Base (MOTOR1) gira nos 2 sentidos (cap 60% no C3).
- [ ] Sentidos ◀/▶ fazem sentido (se invertido: flag "inv" via /save).
- [ ] (opcional) alargar gama da garra: SERVO_LIMITS[3] em c3/config.py.

### Como mexer no C3 (firmware deles não larga o REPL)
O `boot.py` deles corre `/app/rc_main.py` em loop; `mpremote` não entra. Usar os
helpers em `c3/` (entram com spam de Ctrl-C por pyserial):
- `python c3/c3_raw.py COM18 eval "<código>"` — corre código no C3
- `python c3/c3_put.py COM18 local:/remoto ...` — upload (chunked, binário)
- `python c3/c3_backup.py COM18 ./backup_cyberbrick` — backup total
Reverter para o oficial: copiar `/boot_orig.py` -> `/boot.py` e reset.
- [ ] (futuro) movimento suave via set_angle_stepping + timer
- [ ] (futuro) poses guardadas (home/pegar/largar)
- [ ] (futuro) fase 2 — webcam + AI no Raspberry Pi -> HTTP no S3

## DEBUG — "não mexe" mas software OK (resolvido 2026-06-09)

Sintoma: comandos chegam (rx/sent sobem), recetor C3 corre, set_angle/duty_ns
executam sem erro, MAS nenhum servo mexe — nem por comando direto no REPL.
CAUSA: **bateria descarregada**. Os servos/motor só têm força pela BATERIA; o
USB alimenta só a lógica do ESP32. Bateria fraca = sinal PWM sai, movimento não.
Pista: a lógica/REPL viva por USB engana. Se nada mexe nem com `duty_ns` direto,
**suspeita logo de energia dos atuadores** (bateria/interruptor/conector), não do
código. C3 corre **MicroPython 1.23-preview** (S3 = 1.28). `PWM.duty()` getter é
não-fiável (lê 512); o setter funciona à mesma.

## Segurança mecânica — IMPORTANTE

NÃO mandar 0°/180° às cegas: a junta pode não chegar lá e força/queima o servo
ou parte a peça impressa. A UI usa limites conservadores (30–150°). O guarda
mecânico REAL vive em **`c3/config.py`** (clamp no C3, último passo antes do
servo). Os limites em `s3/apps/larm.py` (`SERVOS`) são só para a UI — manter iguais.

## Deploy

**S3 (comando)** — já feito; para re-deploy (`PORT` = COM17 no PC do Trinity;
no Windows usar `python -m mpremote`):
```bash
python -m mpremote connect PORT fs cp larm-wifi/s3/apps/larm.py :apps/larm.py
python -m mpremote connect PORT fs cp larm-wifi/s3/main.py :main.py   # regista a app + arranca o stream
python -m mpremote connect PORT reset
```
Backup do `main.py` original em `larm-wifi/s3/backup_main_s3.py`. A calibração
viva está em `:larm_cal.json` no S3 (cópia de referência em `s3/larm_cal.json`
— ATENÇÃO: home das juntas contínuas = neutro ~92, NÃO é pose).

**C3 (recetor no braço)** — o firmware deles não larga o REPL, usar os helpers
(o recetor vive em **/larm_recv.py** no device, importado pelo nosso /boot.py):
```bash
python larm-wifi/c3/c3_put.py COM18 larm-wifi/c3/main.py:/larm_recv.py larm-wifi/c3/config.py:/config.py
```
Sem credenciais WiFi no C3 (só ESP-NOW). Limites em `c3/config.py`. O canal
descobre-se sozinho (beacon); `/larm_ch.txt` no C3 guarda o último que funcionou.
