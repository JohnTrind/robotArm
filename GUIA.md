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
A página tem **3 tabs** — Controlo / Poses / Calibração — com STOP TUDO e HOME
sempre visíveis.

- **STOP TUDO** (vermelho) e **HOME** (stop + garra na pose inicial).
- **Calibração** (tab própria): marca o **INÍCIO** do curso (braço recolhido,
  = o zero) e o **FIM** (esticado). O "ir para pose" passa a não sair de
  0..FIM nos eixos calibrados. Fica guardado; após reiniciar o S3 basta voltar
  à mesma posição de início e marcar só o INÍCIO.
- **Poses (dead reckoning)**: o S3 *estima* onde cada eixo está integrando a
  velocidade que mandou × tempo. Setup humano: levas o braço à mão a uma posição
  de referência → **ZERO aqui**. Depois moves e **guardas poses** com nome; o
  botão ▶ volta lá sozinho (loop fechado sobre a estimativa). A estimativa
  **deriva** com o uso (sem sensores!) — re-zera quando precisares; reiniciar o
  S3 perde-a. Qualquer comando manual / STOP TUDO cancela um "ir para pose".
- **Animações**: encadeiam poses guardadas com pausas. Na tab Poses: escolhe
  pose → pausa (ms) → "+ passo" → repete → dá nome e Guarda. ▶ corre pose a
  pose. Persiste no S3; cancela com STOP TUDO / qualquer comando manual.
- **HOME**: se existir uma pose chamada **`ground`** (braço recolhido + garra
  fechada), HOME vai até ela por dead reckoning; senão só pára + garra à pose
  inicial.
- Nota de força: o ombro/cotovelo só **levantam** em rápido (em lento/médio não
  têm binário) — o "ir para pose" já usa 100% nas juntas por isso. A base em
  lento dá jeito para afinar a rotação.
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
| `GET /app/larm/state` | posição estimada + lista de poses + goto a correr? |
| `GET /app/larm/zero` | "estás na referência": estimativa de todos os eixos = 0 |
| `GET /app/larm/posesave?name=` | guarda a pose atual (estimativa + garra) |
| `GET /app/larm/posego?name=` | vai para a pose (loop fechado, teto 25s) |
| `GET /app/larm/posedel?name=` | apaga a pose |
| `GET /app/larm/calset?which=min\|max` | marca início (=zero) / fim do curso |
| `GET /app/larm/calclear` | limpa a calibração do curso |
| `POST /app/larm/animsave` | guarda animação `{"name":x,"steps":[{"p":pose,"d":ms}]}` |
| `GET /app/larm/animgo?name=` / `animdel?name=` | corre / apaga animação |
| `POST /app/larm/goto` | alvo direto `{"s1":u,"s2":u,"m1":u,"p3":ang}` (fase AI) |

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

## Fase 2 — o braço a VER (autonomia)

A ideia: uma câmara olha para o braço e para um objeto, o PC calcula para onde
mexer, e manda comandos ao S3. A câmara faz de "olhos" — é o feedback que o
hardware não tem. Marca-se a garra e o objeto com **marcadores ArUco** (uns
quadrados de papel; gera com `host/make_markers_pdf.py`, imprime a 100%).

Corre no PC (`host/`):
- `python vision.py` — só mostra o que reconhece (não mexe o braço).
- `python vision.py --point` — câmara **de cima**: roda a base até a garra
  **apontar** ao objeto. Funciona. (Move o objeto → o braço segue.)
- `python vision.py --reach` — tenta **sobrepor** a garra ao objeto combinando
  base + juntas. Escrito, ainda por testar bem.
- `--src 0` usa uma **webcam** em vez do Pixel (recomendado — ver abaixo).

O que aprendemos no hardware:
- A base e as juntas **seguram a posição** (não caem com o peso). Mas **não há
  sensor** — o software não sabe o ângulo, só a câmara é que "vê".
- **Uma câmara fixa vê bem só um plano**: de cima vê a base a rodar; de lado vê
  as juntas a esticar. O `--reach` contorna isto medindo o que cada atuador faz.
- **"Apontar/seguir" é fiável; "pegar e largar" não** — sem encoders nas juntas,
  precisão fina é impossível. É o teto deste braço.

## Estado / o que ainda falta afinar

- ✅ V1 (controlo manual) fechada — UI, failsafe em camadas, canal automático.
- ✅ Fase 2: pipeline de visão + primitiva "apontar" a funcionar; "alcançar"
  (jacobiano) escrita. Ver `CLAUDE.md > Fase 2`.
- ⚠️ **Câmara do Pixel é muito frágil** (Waydroid). Recomendado: **webcam USB**.
- ❌ **Sem feedback de posição nas juntas** — limite do hardware.

## Próximos passos possíveis

1. **Webcam USB** em vez do Pixel → destrava a fase 2 (`--src 0`).
2. Marcadores mais para fora no braço (mais precisão) e em vários ângulos.
3. **Cérebro Ollama** (local, na RTX 4060) a comandar o "apontar": frame → "vira
   para o cubo" → chama a primitiva.
