# Câmara do Pixel 3a XL (Ubuntu Touch + Waydroid) → PC

A câmara do Pixel é o **input da fase 2** (autonomia por visão). Corre a app
Android **IP Camera** dentro do Waydroid; o PC lê o stream MJPEG por uma ponte
`adb`. Tudo isto foi montado e validado em 2026-06-10.

## A cadeia

```
camara do Pixel
  -> app "Android IP Camera" (Waydroid)  serve https://192.168.240.112:4444/stream
     (192.168.240.x = rede INTERNA do Waydroid, so existe dentro do telemovel)
  -> relay python no telemovel  127.0.0.1:4445 -> 192.168.240.112:4444  (raw TCP)
  -> adb forward  PC:4444 -> telemovel:4445
  -> PC le  https://localhost:4444/stream   (TLS termina aqui; cert self-signed)
```

Porquê o relay: o `adb forward` só chega ao `localhost` do telemóvel, não à rede
interna do Waydroid. O relay (raw TCP, o TLS passa transparente) faz essa ponte.

## Arrancar (com o Pixel por USB, depuração USB ON)

```bash
# 1. Waydroid + app a correr (ver "Reiniciar Waydroid" se preciso)
# 2. ponte:
python host/pixel_cam.py            # arranca relay + forward e testa
# 3. visao:
python host/vision.py               # preview + overlay (NAO mexe o braco)
python host/vision.py --control     # fecha o loop: roda a base p/ centrar o alvo
python host/pixel_cam.py --stop     # desligar a ponte
```

## Gotchas (todos reais, custaram tempo)

- **A app EXIGE username+password "válidos".** Sem credenciais (ou com uma fraca
  tipo `0000`) devolve `403 SECURITY ERROR: credentials not properly configured`
  e não serve frame nenhum. Põe user+password decentes na UI da app (≥8 chars).
- **É HTTPS, não HTTP**, com certificado self-signed. No browser dá aviso; no
  código usa-se `ssl._create_unverified_context()`. Endpoint do vídeo: `/stream`
  (MJPEG `multipart/x-mixed-replace`); a raiz `/` é só a página HTML.
- **A sessão Waydroid morre se a lançares presa ao shell adb.** Lançar destacada:
  `adb shell "(setsid waydroid session start >/tmp/wd.log 2>&1 </dev/null &)"`.
  Comandos `waydroid` precisam do env: `XDG_RUNTIME_DIR=/run/user/32011`,
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/32011/bus`, `WAYLAND_DISPLAY=wayland-0`.
- **`pkill -f relay.py` mata o próprio shell** (o texto "relay.py" está no argv do
  shell). Usar `pkill -f '[r]elay.py'` (truque do bracket).
- **`python3` existe no host UT, NÃO dentro do container Android.** As sondas
  correm no host (`adb shell python3 ...`), não no `waydroid shell`.
- Performance medida: **~9.5 FPS @ 640×480**. Chega para centrar a base. Para
  subir: parâmetros da app via `GET /?resolution=...`, `/?delay=...` (ver o JS
  da página da app).

## Marcadores ArUco

`vision.py` usa o dicionário **4x4_50**. Gera/imprime em
<https://chev.me/arucogen> (Dictionary = 4x4, ID = 0 para o alvo). Cola o
quadrado num objeto e aponta a câmara. `python host/vision.py --list` imprime os
IDs que a câmara está a ver (útil para confirmar deteção).
