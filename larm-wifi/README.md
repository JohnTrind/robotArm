# L-ONE WiFi Control

Controlo wireless de um braço robótico **CyberBrick L-ONE** (Bambu Lab),
substituindo o remote/app oficial. Dois andares:

- **Fase 1 — controlo manual** (fechado): o telemóvel/PC abre uma UI web servida
  por um **ESP32-S3** (comando), que manda ESP-NOW unicast para o **ESP32-C3** no
  braço (recetor, mexe servos + motor).
- **Fase 2 — visão / autonomia** (em curso): o PC olha para uma câmara (OpenCV +
  marcadores ArUco) e comanda o braço pela API HTTP do S3. A câmara é o feedback
  de posição que o hardware não tem.

**Lê o `CLAUDE.md` para o contexto completo** (hardware, decisões, estado, fase 2,
gotchas). O `GUIA.md` explica o sistema em linguagem simples.

## Estrutura

```
s3/apps/larm.py     app do comando (UI + WebSocket + stream ESP-NOW). Deploy no S3.
s3/main.py          arranque do S3 (regista a larm + arranca o stream/beacon)
s3/larm_cal.json    cópia de referência da calibração (a viva está no S3)
c3/main.py          recetor ESP-NOW c/ watchdog + canal automático (-> /larm_recv.py)
c3/config.py        limites mecânicos + neutros (último guarda antes do hardware)
c3/bbl/             drivers oficiais de servos/motores (Python aberto)
c3/c3_*.py          helpers de deploy/debug do C3 (o firmware deles não larga o REPL)
c3/backup_cyberbrick/   backup COMPLETO do firmware original do C3
host/vision.py      FASE 2: visão + controlo autónomo (ArUco, apontar, alcançar)
host/pixel_cam.py   ponte da câmara do Pixel (Waydroid) -> PC
host/make_markers_pdf.py   gera PDF A4 de marcadores ArUco p/ imprimir
host/larm.py        cliente Python (one-shots da API)
host/*.md           docs da fase 2 (vision.md, pixel_camera.md)
docs/               arquitetura A antiga (arquivada)
```

## Usar

**Fase 1 (manual):** UI em `http://192.168.1.79/app/larm/` (IP do S3 na LAN);
API/Postman em `host/`. Ver `GUIA.md`.

**Fase 2 (visão):** ver `host/vision.md`. Resumo:
```
python host/make_markers_pdf.py     # imprime marcadores (100%, sem fit-to-page)
python host/vision.py --src 0       # webcam: preview do que reconhece
python host/vision.py --point --src 0   # roda a base p/ apontar ao objeto
```

## Deploy

Ver `CLAUDE.md > Deploy` (S3 via `python -m mpremote` em COM17; C3 via
`host`... `c3/c3_put.py` em COM18 — **backup primeiro**).

## Segurança

- Limites mecânicos conservadores em `c3/config.py` (clamp no C3).
- Failsafe em camadas: setpoints com expiry no S3 + watchdog de 600 ms no C3 —
  perder WiFi/WS/pacotes nunca deixa o braço a rodar.
- A visão manda sempre comandos **time-boxed** (param sozinhos).
- NÃO mandar 0°/180° às cegas (pode forçar/partir peças impressas).
