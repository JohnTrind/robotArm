#!/usr/bin/env python3
"""make_markers_pdf.py -- folha A4 imprimivel com marcadores ArUco do braco.

Usa o MESMO dicionario do vision.py (4x4_50) -> o que imprimes e exatamente o
que o detetor procura. Cada marcador sai a um tamanho fisico EXATO (mm), com
etiqueta e linha de corte. Imprime a 100% / "tamanho real" (sem "fit to page",
senao o tamanho em mm fica errado e estraga a estimativa de distancia).

Uso:
  python make_markers_pdf.py                      # IDs 0-3, 70mm, -> markers.pdf
  python make_markers_pdf.py --size-mm 80 --ids 0 1
  python make_markers_pdf.py --out /tmp/aruco.pdf
"""
import argparse

import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ARUCO_DICT = cv2.aruco.DICT_4X4_50   # tem de bater com vision.py
A4_W, A4_H = 210.0, 297.0            # mm


def marker_img(idx, px=600):
    d = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    img = cv2.aruco.generateImageMarker(d, idx, px)
    return img   # grayscale, borda preta incluida


def build(ids, size_mm, out):
    cols = 1 if len(ids) == 1 else 2
    rows = (len(ids) + cols - 1) // cols
    gap_x = (A4_W - cols * size_mm) / (cols + 1)
    label_h = 10.0
    cell_h = size_mm + label_h
    gap_y = (A4_H - rows * cell_h) / (rows + 1)

    fig = plt.figure(figsize=(A4_W / 25.4, A4_H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, A4_W)
    ax.set_ylim(0, A4_H)
    ax.invert_yaxis()                 # origem no topo-esquerda
    ax.set_aspect("equal")
    ax.axis("off")

    for n, mid in enumerate(ids):
        r, c = divmod(n, cols)
        x = gap_x + c * (size_mm + gap_x)
        y = gap_y + r * (cell_h + gap_y)
        ax.imshow(marker_img(mid), cmap="gray", vmin=0, vmax=255,
                  extent=(x, x + size_mm, y + size_mm, y),
                  interpolation="nearest", zorder=2)
        # linha de corte fina a toda a volta do marcador
        ax.add_patch(Rectangle((x, y), size_mm, size_mm, fill=False,
                               edgecolor="#bbbbbb", lw=0.4, zorder=3))
        ax.text(x + size_mm / 2, y + size_mm + 6,
                "ID %d   4x4   %.0f mm" % (mid, size_mm),
                ha="center", va="center", fontsize=9, family="monospace")

    ax.text(A4_W / 2, max(4, gap_y / 2),
            "ArUco 4x4_50 - braco L-ONE - IMPRIMIR A 100%% (tamanho real)",
            ha="center", va="center", fontsize=8, color="#666666")
    fig.savefig(out, format="pdf")
    plt.close(fig)
    print("[markers] %d marcadores (%.0fmm) -> %s" % (len(ids), size_mm, out))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--size-mm", type=float, default=70.0)
    ap.add_argument("--out", default="markers.pdf")
    a = ap.parse_args()
    build(a.ids, a.size_mm, a.out)
