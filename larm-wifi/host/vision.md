# Fase 2 — Visão / Autonomia (`vision.py`)

A câmara fecha o loop de posição que o braço não tem (juntas contínuas + motor
DC, sem encoders). O PC vê marcadores **ArUco**, calcula o erro, e manda comandos
**time-boxed** à API HTTP do S3 (que retransmite por ESP-NOW). Corre no PC.

Fonte de vídeo: ver `pixel_camera.md`. **Recomendado: webcam USB (`--src 0`).**

## Marcadores

- Dicionário **4x4_50**. Gera a folha A4: `python make_markers_pdf.py`
  (`--size-mm 80 --ids 0 1` p/ afinar). **Imprime a 100% / tamanho real.**
- **ID 0 → na GARRA**, **ID 1 → no OBJETO** (defaults). Colar planos e virados
  para a câmara. Dica: vários marcadores na garra em ângulos diferentes → um
  está sempre visível enquanto o braço roda. E quanto **mais para fora** no braço
  o marcador da garra estiver, **mais precisão** (menos ruído angular).
- `python vision.py --list` — imprime os IDs que a câmara vê (confirmar deteção).

## Modos

| comando | o que faz | câmara | mexe o braço? |
|---|---|---|---|
| `vision.py` | preview + overlay do que reconhece | qualquer | NÃO |
| `vision.py --point` | roda a base até a garra **apontar** ao objeto | **top-down** | sim (base) |
| `vision.py --reach` | combina base+juntas até a garra **sobrepor** o objeto | qualquer | sim (tudo) |
| `vision.py --calibrate-pivot` | (re)deteta o centro de rotação da base | top-down | sim (base) |

Flags úteis: `--src 0` (webcam), `--gripper-id N`, `--target-id N`, `--s3 URL`.
**Ctrl-C** sai sempre a parar o braço.

### `--point` (APONTAR) — PROVADO
Câmara de cima. Auto-deteta o **pivot** da base (ajusta um círculo ao arco que a
garra traça ao rodar; guarda em `larm_pivot.json`). Depois roda a base até o
ângulo garra↔pivot bater com o ângulo objeto↔pivot. Histerese para assentar sem
"caçar". Converge em ~5 passos. **Precisão ~±15°** (limitada pelo marcador estar
perto do centro de rotação — pôr mais para fora melhora).

### `--reach` (ALCANÇAR) — escrito, por validar
**Jacobiano de imagem** (uncalibrated visual servoing): sonda cada atuador
(base, junta1, junta2), mede o vetor que faz a garra na imagem, monta a matriz
2×3, e usa a pseudo-inversa para calcular que combinação leva a garra ao objeto.
**Funciona em qualquer ângulo** (mede o efeito real de cada atuador). Defesas:
passos pequenos, **recuo automático** se a garra sai do enquadramento, aviso de
reframe ao fim de 3 perdas. Re-sonda a cada 5 passos (a pose muda).

## Factos de hardware (descobertos na sessão)

- **A base e as juntas SEGURAM a posição** (não caem com o peso). Sem sensor → o
  software não sabe o ângulo, mas o braço não colapsa.
- **Uma câmara fixa vê bem só um plano.** Top-down vê a base (horizontal) mas
  quase não vê as juntas a esticar (vertical ≈ Z). De lado vê as juntas, perde a
  base. Por isso `--point` é top-down e `--reach` usa o Jacobiano (qualquer ângulo).
- **Teto honesto:** "apontar/seguir" é fiável; **"pegar e largar" não** sem
  feedback nas juntas (encoders). Seria projeto de hardware.

## Próximo: cérebro Ollama (planeado)

Local, na RTX 4060 (8GB). Modelo p/ começar: `moondream` (rápido). VLM =
**planeador lento** (frame → "aponta para o cubo vermelho") por cima das
primitivas rápidas (`--point`/`--reach`). NÃO servoing direto com o VLM (lento).
Um loop Python a chamar a API do S3 chega — sem MCP.
