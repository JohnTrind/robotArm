# L-ONE WiFi Control

Controlo wireless de um braço robótico CyberBrick L-ONE (Bambu Lab):
o telemóvel/PC fala com um **ESP32-S3** (comando, serve a UI web) que manda
ESP-NOW unicast para o **ESP32-C3** no braço (recetor, mexe servos + motor).
Substitui o remote/app oficial. Caminho para AI+webcam (fase 2).

**Lê o `CLAUDE.md` para o contexto completo** (factos de hardware, decisões,
estado). O `GUIA.md` explica como o sistema funciona em linguagem simples.

## Estrutura

```
s3/apps/larm.py   app do comando (UI + WebSocket + stream ESP-NOW). Deploy no S3.
s3/main.py        arranque do S3 (regista a larm + arranca o stream/beacon)
s3/larm_cal.json  cópia de referência da calibração (a viva está no S3)
c3/main.py        recetor ESP-NOW c/ watchdog + canal automático (-> /larm_recv.py)
c3/config.py      limites mecânicos + neutros (o último guarda antes do hardware)
c3/bbl/           drivers oficiais de servos/motores (Python aberto)
c3/c3_*.py        helpers de deploy/debug do C3 (o firmware deles não larga o REPL)
c3/backup_cyberbrick/  backup COMPLETO do firmware original do C3
host/             cliente Python + coleção Postman (base da fase AI)
docs/             arquitetura A antiga (arquivada)
```

## Usar

1. UI: `http://192.168.1.79/app/larm/` (IP do S3 na rede de casa)
2. API: ver tabela no `GUIA.md`; coleção Postman em `host/`

## Deploy

Ver `CLAUDE.md > Deploy` (S3 via mpremote em COM17; C3 via `c3/c3_put.py` em
COM18 — backup primeiro!).

## Segurança

- Limites mecânicos conservadores em `c3/config.py` (clamp no C3).
- Failsafe em camadas: setpoints com expiry no S3 + watchdog de 600 ms no C3 —
  perder WiFi/WS/pacotes nunca deixa o braço a rodar.
- NÃO mandar 0°/180° às cegas (pode forçar/partir peças impressas).
