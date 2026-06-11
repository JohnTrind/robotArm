# Câmara para a Fase 2 — Pixel (Waydroid) ou webcam

A fase de visão (`vision.py`) precisa de um stream de vídeo. Há dois caminhos:

- **Webcam USB / câmara do portátil — RECOMENDADO.** `vision.py --src 0` salta
  TODA a canalização abaixo. É de longe o mais fiável. Usa isto se puderes.
- **Câmara do Pixel 3a (Ubuntu Touch + Waydroid)** — funciona mas é **muito
  frágil** (ver "Fragilidade" no fim). Documentado aqui porque foi o que se usou.

---

## Pixel: a cadeia

```
câmara do Pixel
  -> app "Android IP Camera" (Waydroid)  serve https://192.168.240.112:4444/stream
     (192.168.240.x = rede INTERNA do Waydroid, só existe dentro do telemóvel)
  -> relay python no telemóvel  127.0.0.1:4445 -> 192.168.240.112:4444  (raw TCP)
  -> adb forward  PC:4444 -> telemóvel:4445
  -> PC lê  https://localhost:4444/stream   (TLS termina aqui; cert self-signed)
```

Porquê o relay: o `adb forward` só chega ao `localhost` do telemóvel, não à rede
interna do Waydroid. O relay (raw TCP, o TLS passa transparente) faz a ponte.

## Arrancar (Pixel por USB, depuração USB ON)

```bash
python host/pixel_cam.py        # arranca relay + adb forward e testa o /stream
python host/vision.py --point   # (ou outro modo) — ver host/vision.md
python host/pixel_cam.py --stop # desligar a ponte
```

Se o `pixel_cam.py` falhar, segue "Recuperar" abaixo.

## Recuperar (a sessão / app caíram)

Variáveis de ambiente que TODOS os comandos `waydroid` precisam:
```
XDG_RUNTIME_DIR=/run/user/32011
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/32011/bus
WAYLAND_DISPLAY=wayland-0
```

1. **Sessão Waydroid parada?** Arranca-a destacada **com as env vars** (sem elas
   dá `Unable to autolaunch a dbus-daemon without a $DISPLAY`):
   ```bash
   adb shell "(setsid env XDG_RUNTIME_DIR=/run/user/32011 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/32011/bus WAYLAND_DISPLAY=wayland-0 waydroid session start >/tmp/wd.log 2>&1 </dev/null &)"
   # confirmar: adb shell "export XDG_RUNTIME_DIR=...; waydroid status"  -> RUNNING
   ```
2. **App em background?** (o stream pára se o launcher estiver à frente). Relança
   e confirma o foreground:
   ```bash
   adb shell "export <env>; waydroid app launch com.github.digitallyrefined.androidipcamera"
   # confirmar: dumpsys activity activities | grep topResumed  -> ...androidipcamera/.MainActivity
   ```
3. **`/stream` dá 503** ("client already connected", de uma ligação que travou):
   reinicia o relay (`python pixel_cam.py`) e, se persistir, `am force-stop` +
   relança a app.
4. **`/stream` dá SSL EOF / frames travam:** o HAL da câmara encravou. Reiniciar a
   sessão Waydroid às vezes cura; se não, **reinicia o telemóvel**.

## Gotchas (todos reais)

- **A app EXIGE username+password válidos.** Sem credenciais (ou fracas tipo
  `0000`) → `403 SECURITY ERROR: credentials not properly configured`, não serve
  frame. Põe user+password decentes na UI da app.
- **É HTTPS self-signed.** No código: `ssl._create_unverified_context()`.
  Endpoint do vídeo: `/stream` (MJPEG `multipart/x-mixed-replace`); `/` é a página.
- **`pkill -f relay.py` mata o próprio shell** (o texto está no argv). Usar
  `pkill -f '[r]elay.py'` (truque do bracket).
- **`python3` existe no host UT, NÃO no container Android.** As sondas correm no
  host (`adb shell python3 ...`), não no `waydroid shell`.
- Performance: **~9.5 FPS @ 640×480**. Chega para o controlo lento da visão.

## Fragilidade (porque a webcam é melhor)

A app no Waydroid precisa de estar em foreground e o HAL da câmara encrava com
facilidade (503 / SSL EOF / frames a travar), por vezes só curável com reboot do
telemóvel. Gastou muito tempo de sessão. **Para desenvolvimento, uma webcam USB
(`vision.py --src 0`) é muito menos dor de cabeça e o pipeline é idêntico.**

## Marcadores ArUco

`vision.py` usa o dicionário **4x4_50**. Gera a folha imprimível com
`python host/make_markers_pdf.py` (imprime a 100% / tamanho real). `vision.py
--list` imprime os IDs que a câmara está a ver.
