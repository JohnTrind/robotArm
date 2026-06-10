# GUIA — L-ONE WiFi Control (como tudo funciona)

Visão rápida do sistema para explorares. Detalhe técnico fino → `CLAUDE.md`.

## A ideia

Controlar o braço **CyberBrick L-ONE** sem o remote/app oficial: um telemóvel
abre uma página web, mexe sliders/botões, e o braço obedece — tudo por WiFi.

## Como está montado (a cadeia)

```
[telemóvel/PC]  --WiFi: HTTP + WebSocket-->  [ESP32-S3]  --ESP-NOW-->  [ESP32-C3]  --PWM-->  servos+motor
   browser                                    "comando"                "recetor"            (o braço)
```

- **ESP32-S3** (placa do `espMP3`, porta **COM17**, IP **192.168.1.79**): é o
  *comando*. Corre a app `larm` e serve a UI. Está na tua secretária, ligado por USB.
- **ESP32-C3** (dentro da base do braço, porta **COM18** quando ligado por USB):
  é o *recetor*. Só ouve ESP-NOW e mexe os atuadores. Não sabe WiFi nenhuma.
- **ESP-NOW**: link peer-to-peer entre S3↔C3 (em cima do rádio WiFi, **não é
  Bluetooth**). Por isso o braço nunca precisa de saber redes — só o S3.

## Os atuadores (IMPORTANTE — muda como controlas)

| idx | o que é | tipo | controlo |
|-----|---------|------|----------|
| 1 | Junta 1 | servo **contínuo** | **velocidade** (não posição) |
| 2 | Junta 2 | servo **contínuo** | **velocidade** |
| 3 | Claw | servo **posicional** | **ângulo** (sabe a posição) |
| MOTOR1 | Base (yaw) | **motor DC** | **velocidade** |

> **Sem sensores.** As juntas e a base não sabem onde estão — só obedecem a
> *velocidade*. "50" = 50% de velocidade, roda até mandares parar. Só a **claw**
> tem ângulo real. Para um movimento fixo nos de velocidade → roda durante um
> **tempo** (`&ms=400`). Distância ≈ velocidade × tempo.

## Como o controlo viaja

1. **WebSocket** (`ws://192.168.1.79:81`) — caminho rápido em tempo real. A UI
   manda a **intenção** (premir/largar + heartbeat 300ms); o S3 faz stream do
   setpoint a ~30Hz para o C3. É isto que usas a sério.
2. **HTTP** (`/app/larm/...`) — fallback + automação (Postman, Raspberry Pi/AI).
   Sempre **time-boxed**: um comando contínuo roda no máx. `ms` (default 1500,
   cap 3000) e pára sozinho.
3. **Animações** — sequências que correm **dentro do S3** (timing local, fluido).

**Canal automático**: o S3 anuncia o canal num beacon; o C3 encontra-o sozinho
(scan + `/larm_ch.txt`). Router mudar de canal deixou de partir o sistema.

Segurança em camadas — nada fica a rodar sozinho:
- largar o botão / fechar o browser / perder o WS → setpoint volta a neutro;
- **watchdog no C3**: 600ms sem rádio com algo a rodar → pára sozinho (cobre
  pacote de STOP perdido e S3 morto). A garra nunca é mexida pelo watchdog.

## A UI (no telemóvel: http://192.168.1.79/app/larm/)

- **Base / Cotovelo / Ombro**: ◀/■/▶ — manter premido para mover (o botão
  acende), largar = parar. ■ = stop da fila.
- **Garra**: fechar/abrir + slider com o valor em graus.
- **Velocidade**: lento / médio / rápido (afeta juntas e base; fica guardado).
- **STOP TUDO** (vermelho) e **HOME** (stop + garra na pose inicial).
- Pílula no topo: verde = WebSocket ligado, mostra canal e RTT.
- No PC dá para usar o **teclado**: ←/→ base, q/a e w/s juntas, e/d garra,
  espaço = STOP. Esconder o separador também pára tudo.

## A API HTTP (resumo)

| endpoint | faz |
|----------|-----|
| `GET /app/larm/config` | estado: atuadores, canal, contadores |
| `GET /app/larm/move?idx=&angle=&ms=` | servo (claw=ângulo; contínuo=velocidade; `ms`=nudge fixo) |
| `GET /app/larm/stop?idx=` | pára servo (vai ao neutro) |
| `GET /app/larm/stopall` | pára juntas + base (não mexe a garra) |
| `GET /app/larm/motor?idx=&pct=&ms=` | motor base (pct -100..100; `ms`=nudge fixo) |
| `GET /app/larm/home` | pára tudo + garra na pose inicial |
| `POST /app/larm/seq` | corre uma animação (lista de passos) |
| `GET /app/larm/seqstop` | pára a animação |

Coleção Postman pronta: `host/larm.postman_collection.json`.

## Estrutura dos ficheiros

```
s3/apps/larm.py   <- a app do comando (UI + WebSocket + ESP-NOW). Deploy no S3.
s3/main.py        <- arranque do S3 (regista a larm). backup: s3/backup_main_s3.py
c3/main.py        <- recetor ESP-NOW. Deploy no C3 (como /larm_recv.py).
c3/config.py      <- limites + canal ESP-NOW do C3.
c3/bbl/servos.py  <- driver de servos (oficial, aberto).
c3/backup_cyberbrick/  <- backup COMPLETO do firmware original do C3.
host/larm.py      <- cliente Python (base da fase AI).
```

Ferramentas de deploy/debug do C3 (o firmware deles não larga o REPL): `c3/c3_raw.py`,
`c3/c3_put.py`, `c3/c3_backup.py`, `c3/c3_diag.py`.

## Estado / o que ainda falta afinar

- ✅ V1 fechada (2026-06-10): bugs do "continua a rodar" corrigidos (tipo de
  controlo, neutro da calibração, canal ESP-NOW desencontrado), unicast,
  watchdog no C3, canal automático, UI nova. Ver `CLAUDE.md > Estado atual`.
- ⚠️ **Confirmar com a bateria ligada**: largar pára mesmo, sentidos ◀/▶,
  neutro fino de cada junta (se "zumbir" parada, afinar home via /save).
- ⚠️ **Bateria** — os servos só têm força pela bateria do braço (o USB alimenta só
  a lógica). Se "responde ok mas não mexe", é quase sempre bateria fraca → carregar.
- ❌ **Sem feedback de posição** — não dá saber limites por software (precisaria
  microswitch/current-sense). "Casa" do braço = batente mecânico (dobrado).

## Próximos passos possíveis

1. Afinar sentidos/velocidades (flag `inv` por atuador) e animações com juntas.
2. Fase 2: cérebro (webcam + AI) no Raspberry Pi / Pixel Ubuntu Touch, a falar a
   mesma API HTTP/WS.
