#!/usr/bin/env python3
"""
Repara videos Sony (XAVC S / XAVC HS) recuperados de una tarjeta formateada.

Problema: al recuperar por firma, cada video sale con su imagen y sonido (mdat)
pero SIN su índice (moov), porque la cámara Sony guarda el índice en otra parte
de la tarjeta (a menudo justo antes del inicio del video). Sin índice, ningún
programa puede abrir el video.

Solución: este programa
  1. busca en la imagen de la tarjeta todos los inicios de video Sony y todos
     los índices,
  2. para cada índice comprueba, fotograma por fotograma, a qué video pertenece
     (cada fotograma H.264/H.265 tiene una estructura que se puede verificar),
  3. une el video con su índice y lo guarda listo para reproducir, con la fecha
     y hora de grabación en el nombre.

Uso:
  python3 reparar_videos_sony.py  tarjeta.img  carpeta_salida
"""

import os
import re
import struct
import sys
import time
from datetime import datetime, timedelta

MB = 1024 * 1024
BLOQUE = 64 * MB


# ------------------------------------------------------------------ utilidades

def leer(f, pos, n):
    f.seek(pos)
    return f.read(n)


def cajas(datos, ini=0, fin=None):
    """Itera las cajas MP4 (tamaño, tipo, inicio del contenido, fin) de un bloque."""
    fin = len(datos) if fin is None else fin
    p = ini
    while p + 8 <= fin:
        tam, tipo = struct.unpack(">I4s", datos[p:p + 8])
        cab = 8
        if tam == 1:
            tam = struct.unpack(">Q", datos[p + 8:p + 16])[0]
            cab = 16
        elif tam == 0:
            tam = fin - p
        if tam < cab or p + tam > fin:
            return
        yield tipo, p + cab, p + tam
        p += tam


CONTENEDORES = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}


def analizar_moov(moov):
    """Devuelve info del índice: pistas con offsets y tamaños de cada muestra."""
    info = {"pistas": [], "fecha": None, "duracion": 0.0}

    def recorrer(ini, fin, pista):
        for tipo, a, b in cajas(moov, ini, fin):
            if tipo == b"trak":
                nueva = {}
                recorrer(a, b, nueva)
                info["pistas"].append(nueva)
            elif tipo in CONTENEDORES:
                recorrer(a, b, pista)
            elif tipo == b"mvhd":
                v = moov[a]
                if v == 1:
                    creado, _, escala, dur = struct.unpack(">QQIQ", moov[a + 4:a + 32])
                else:
                    creado, _, escala, dur = struct.unpack(">IIII", moov[a + 4:a + 20])
                if escala:
                    info["duracion"] = dur / escala
                if creado:
                    info["fecha"] = datetime(1904, 1, 1) + timedelta(seconds=creado)
            elif tipo == b"hdlr":
                pista["tipo"] = moov[a + 8:a + 12]
            elif tipo == b"stsd":
                pista["codec"] = moov[a + 12:a + 16]
            elif tipo == b"stsz":
                fijo, n = struct.unpack(">II", moov[a + 4:a + 12])
                pista["tamanos"] = ([fijo] * n if fijo else
                                    list(struct.unpack(f">{n}I", moov[a + 12:a + 12 + 4 * n])))
            elif tipo == b"stco":
                n = struct.unpack(">I", moov[a + 4:a + 8])[0]
                pista["chunks"] = list(struct.unpack(f">{n}I", moov[a + 8:a + 8 + 4 * n]))
            elif tipo == b"co64":
                n = struct.unpack(">I", moov[a + 4:a + 8])[0]
                pista["chunks"] = list(struct.unpack(f">{n}Q", moov[a + 8:a + 8 + 8 * n]))
            elif tipo == b"stsc":
                n = struct.unpack(">I", moov[a + 4:a + 8])[0]
                pista["stsc"] = [struct.unpack(">III", moov[a + 8 + 12 * k:a + 20 + 12 * k])
                                 for k in range(n)]

    recorrer(0, len(moov), None)
    fin = 0
    for p in info["pistas"]:
        p["muestras"] = posiciones_muestras(p)
        if p["muestras"]:
            off, tam = p["muestras"][-1]
            fin = max(fin, off + tam)
    info["fin_datos"] = fin
    info["video"] = next((p for p in info["pistas"]
                          if p.get("tipo") == b"vide" and p.get("codec") in
                          (b"avc1", b"avc3", b"hvc1", b"hev1") and p["muestras"]), None)
    return info


def posiciones_muestras(p):
    tam, chunks, stsc = p.get("tamanos"), p.get("chunks"), p.get("stsc")
    if not tam or not chunks or not stsc:
        return []
    res, k, idx = [], 0, 0
    for c, off in enumerate(chunks, start=1):
        while idx + 1 < len(stsc) and stsc[idx + 1][0] <= c:
            idx += 1
        for _ in range(stsc[idx][1]):
            if k >= len(tam):
                return res
            res.append((off, tam[k]))
            off += tam[k]
            k += 1
    return res


def fotograma_valido(f, pos, tam):
    """Un fotograma H.264/H.265 en MP4 es una serie de bloques [largo de 4 bytes +
    datos] que suman exactamente el tamaño indicado por el índice."""
    off = 0
    while off < tam:
        cab = leer(f, pos + off, 5)
        if len(cab) < 5:
            return False
        largo = struct.unpack(">I", cab[:4])[0]
        if largo == 0 or off + 4 + largo > tam or cab[4] & 0x80:
            return False
        off += 4 + largo
    return off == tam


def comprobar(f, inicio, muestras, indices):
    return all(fotograma_valido(f, inicio + muestras[i][0], muestras[i][1]) for i in indices)


# ------------------------------------------------------------------ pasos

def buscar(f, total):
    inicios, indices = [], []
    p_ftyp = re.compile(rb"ftypXAVC")
    p_moov = re.compile(rb"moov....mvhd", re.S)
    t0 = time.time()
    for base in range(0, total, BLOQUE):
        buf = leer(f, base, BLOQUE + 16)
        for m in p_ftyp.finditer(buf):
            if 4 <= m.start() < BLOQUE:
                inicios.append(base + m.start() - 4)
        for m in p_moov.finditer(buf):
            if 4 <= m.start() < BLOQUE:
                indices.append(base + m.start() - 4)
        hecho = min(total, base + BLOQUE)
        vel = hecho / MB / max(0.001, time.time() - t0)
        print(f"\rBuscando: {hecho * 100 // total:3d}%  ({vel:.0f} MB/s)  "
              f"inicios de video: {len(inicios)}  índices: {len(indices)}", end="", flush=True)
    print()
    return inicios, indices


def cabecera_mdat(f, inicio):
    """Encuentra la caja mdat al inicio del video: (posición, inicio de sus datos)."""
    cab = leer(f, inicio, MB)
    p = 0
    while p + 16 <= len(cab):
        tam, tipo = struct.unpack(">I4s", cab[p:p + 8])
        if tipo == b"mdat":
            return p, p + (16 if tam == 1 else 8)
        if tam < 8:
            return None
        p += tam
    return None


def guardar(f, salida, nombre, inicio, mdat, info, moov, extra):
    pos_mdat, pos_datos = mdat
    fin = info["fin_datos"]
    ruta = os.path.join(salida, nombre)
    with open(ruta, "wb") as out:
        out.write(leer(f, inicio, pos_mdat))
        tam_mdat = fin - pos_mdat
        if pos_datos - pos_mdat == 16:
            out.write(struct.pack(">I4sQ", 1, b"mdat", tam_mdat))
        else:
            out.write(struct.pack(">I4s", tam_mdat, b"mdat"))
        p = inicio + pos_datos
        while p < inicio + fin:
            n = min(16 * MB, inicio + fin - p)
            out.write(leer(f, p, n))
            p += n
        out.write(moov)
        out.write(extra)
    return ruta


def nombre_libre(salida, base):
    nombre, k = f"{base}.MP4", 2
    while os.path.exists(os.path.join(salida, nombre)):
        nombre = f"{base}_{k}.MP4"
        k += 1
    return nombre


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    img, salida = sys.argv[1], sys.argv[2]
    os.makedirs(salida, exist_ok=True)
    incompletos = os.path.join(salida, "posiblemente_incompletos")
    total = os.path.getsize(img)
    f = open(img, "rb")

    print("PASO 1/2: buscando inicios de video e índices en la tarjeta...")
    inicios, pos_indices = buscar(f, total)
    if not inicios or not pos_indices:
        sys.exit("No se encontraron videos Sony o índices en la imagen.")

    print("PASO 2/2: uniendo cada índice con su video...")
    usados, ok, rotos, sin_video = set(), 0, 0, 0
    t0 = time.time()
    for n, pm in enumerate(pos_indices, 1):
        tam = struct.unpack(">I", leer(f, pm, 4))[0]
        if not 8 < tam < 64 * MB:
            continue
        moov = leer(f, pm, tam)
        try:
            info = analizar_moov(moov)
        except (struct.error, IndexError, ValueError):
            continue
        v = info["video"]
        if not v:
            continue
        extra = b""
        sig = leer(f, pm + tam, 8)
        if len(sig) == 8 and sig[4:8] == b"meta":
            t_meta = struct.unpack(">I", sig[:4])[0]
            if 8 < t_meta < 4 * MB:
                extra = leer(f, pm + tam, t_meta)

        m = v["muestras"]
        ultimo = len(m) - 1
        puntos = sorted({ultimo // 4, ultimo // 2, 3 * ultimo // 4, ultimo})
        candidatos = sorted((s for s in inicios if s not in usados), key=lambda s: abs(s - pm))
        elegido, completo, parcial = None, False, None
        for s in candidatos:
            if s + info["fin_datos"] > total or not comprobar(f, s, m, [0, min(1, ultimo)]):
                continue
            if comprobar(f, s, m, puntos):  # coincide de principio a fin
                elegido, completo = s, True
                break
            if parcial is None:  # empieza bien pero algo falla después: guardar por si acaso
                parcial = s
        if elegido is None:
            elegido = parcial
        if elegido is None:
            sin_video += 1
            continue
        mdat = cabecera_mdat(f, elegido)
        if mdat is None:
            sin_video += 1
            continue
        usados.add(elegido)
        fecha = info["fecha"].strftime("%Y-%m-%d_%H-%M-%S") if info["fecha"] else f"video_{n:04d}"
        carpeta = salida if completo else incompletos
        os.makedirs(carpeta, exist_ok=True)
        nombre = nombre_libre(carpeta, f"Sony_{fecha}")
        guardar(f, carpeta, nombre, elegido, mdat, info, moov, extra)
        if completo:
            ok += 1
        else:
            rotos += 1
        vel = n / max(0.001, time.time() - t0)
        print(f"\r  revisados {n}/{len(pos_indices)}  reparados: {ok}  "
              f"incompletos: {rotos}  ({vel:.1f}/s)   ", end="", flush=True)

    print("\n\nResultado:")
    print(f"  Videos reparados:            {ok}")
    print(f"  Posiblemente incompletos:    {rotos}  (en la subcarpeta posiblemente_incompletos)")
    print(f"  Índices sin video encontrado: {sin_video}  (su video fue sobrescrito o es un archivo proxy)")
    print(f"Guardados en: {os.path.abspath(salida)}")


if __name__ == "__main__":
    main()
