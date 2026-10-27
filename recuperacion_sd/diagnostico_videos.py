#!/usr/bin/env python3
"""
Diagnóstico: para algunos índices (moov) de la tarjeta, busca en TODA la imagen
dónde está realmente su primer fotograma, usando la marca con la que empieza
cada fotograma H.264 de Sony (bloque AUD: 00 00 00 02 09).

Uso: python3 diagnostico_videos.py tarjeta.img [cuantos_indices]
"""
import os
import re
import struct
import sys
import time
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reparar_videos_sony as r  # noqa: E402

MB = 1024 * 1024
B = 64 * MB


def main():
    img = sys.argv[1]
    cuantos = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    total = os.path.getsize(img)
    f = open(img, "rb")
    aud = re.compile(rb"\x00\x00\x00\x02\x09")
    p_moov = re.compile(rb"moov....mvhd", re.S)
    p_ftyp = re.compile(rb"ftyp(?:XAVC|MSNV|mp4[12]|iso[m2-6]|avc1)")
    fotos, indices, inicios = array("Q"), [], []
    t0 = time.time()
    for base in range(0, total, B):
        buf = r.leer(f, base, B + 16)
        for m in aud.finditer(buf):
            if m.start() < B:
                fotos.append(base + m.start())
        for m in p_moov.finditer(buf):
            if 4 <= m.start() < B:
                indices.append(base + m.start() - 4)
        for m in p_ftyp.finditer(buf):
            if 4 <= m.start() < B:
                inicios.append(base + m.start() - 4)
        hecho = min(total, base + B)
        print(f"\rLeyendo: {hecho * 100 // total:3d}%  fotogramas: {len(fotos)}  "
              f"({hecho / MB / max(.001, time.time() - t0):.0f} MB/s)", end="", flush=True)
    print()
    set_inicios = set(inicios)

    for pm in indices[:cuantos]:
        tam = struct.unpack(">I", r.leer(f, pm, 4))[0]
        info = r.analizar_moov(r.leer(f, pm, tam))
        v = info["video"]
        if not v:
            print(f"indice {pm:#x}: sin pista de video")
            continue
        m = v["muestras"]
        k0 = 1 if len(m) > 2 else 0  # se usa el 2.º fotograma (el 1.º puede llevar cabeceras extra)
        (o0, s0) = m[k0]
        hallados = []
        for i in range(len(fotos) - 1):
            # en Sony los fotogramas van pegados: el siguiente empieza justo al terminar
            if fotos[i + 1] - fotos[i] != s0:
                continue
            p = fotos[i]
            if not r.fotograma_valido(f, p, s0):
                continue
            inicio = p - o0
            seguidos = 0
            for k in range(len(m)):
                if r.fotograma_valido(f, inicio + m[k][0], m[k][1]):
                    seguidos += 1
                else:
                    break
            hallados.append((p, inicio, seguidos))
            if len(hallados) >= 3:
                break
        dur = info["duracion"]
        print(f"\nindice {pm:#x} ({tam} bytes, {len(m)} fotogramas, {dur:.1f} s):")
        if not hallados:
            print("   primer fotograma NO encontrado en la tarjeta")
        for p, inicio, seg in hallados:
            cerca = min(inicios, key=lambda s: abs(s - inicio)) if inicios else 0
            print(f"   fotograma {k0} en {p:#x}  -> el video empezaria en {inicio:#x} "
                  f"(resto 128K: {inicio % 131072:#x}, es inicio de archivo: {inicio in set_inicios}, "
                  f"inicio mas cercano {cerca:#x})  fotogramas seguidos OK: {seg}/{len(m)}")


if __name__ == "__main__":
    main()
