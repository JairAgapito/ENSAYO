#!/usr/bin/env python3
"""
Repara videos Sony (XAVC S / XAVC HS) recuperados de una tarjeta formateada.

Al recuperar por firma, los videos Sony salen sin su índice (moov) y además
desordenados: la cámara intercala en la tarjeta otros datos (archivo proxy,
miniaturas...) entre los trozos de cada video. Sin índice ni orden, ningún
programa puede abrirlos.

Este programa:
  1. busca en la imagen de la tarjeta todos los índices de video y la marca con
     que empieza cada fotograma Sony (AUD: 00 00 00 02 09),
  2. para cada índice localiza su primer fotograma y sigue el video fotograma por
     fotograma; cuando encuentra un salto, busca dónde continúa (siempre a un
     múltiplo de 128 KB, el tamaño de bloque de la tarjeta),
  3. une los trozos con su índice y guarda el video listo para reproducir, con la
     fecha y hora de grabación en el nombre.

Uso:
  python3 reparar_videos_sony.py  tarjeta.img  carpeta_salida
"""

import bisect
import os
import re
import struct
import sys
import time
from array import array
from datetime import datetime, timedelta

MB = 1024 * 1024
BLOQUE = 64 * MB
CL = 128 * 1024          # bloque de la tarjeta (cluster exFAT)
VENTANA = 1024 * MB      # hasta dónde buscar la continuación tras un salto


# ------------------------------------------------------------------ utilidades

def leer(f, pos, n):
    f.seek(pos)
    return f.read(n)


def cajas(datos, ini=0, fin=None):
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
    """Pistas del índice con la posición y tamaño de cada muestra."""
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
                if moov[a] == 1:
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
                if b - a >= 44:
                    pista["ancho"], pista["alto"] = struct.unpack(">HH", moov[a + 40:a + 44])
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
    fin, ini = 0, None
    for p in info["pistas"]:
        p["muestras"] = posiciones_muestras(p)
        if p["muestras"]:
            off, tam = p["muestras"][-1]
            fin = max(fin, off + tam)
            ini = p["muestras"][0][0] if ini is None else min(ini, p["muestras"][0][0])
    info["fin_datos"], info["ini_datos"] = fin, ini
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
    if pos < 0:
        return False
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


# ------------------------------------------------------------------ mapa de trozos

class Mapa:
    """Traduce posiciones del video (lógicas) a posiciones en la tarjeta (físicas).
    Cada tramo: (inicio_lógico, inicio_físico); dentro de un tramo los datos van seguidos."""

    def __init__(self, log, fis):
        self.log, self.fis = [log], [fis]

    def copia(self):
        c = Mapa(0, 0)
        c.log, c.fis = list(self.log), list(self.fis)
        return c

    def fisica(self, x):
        i = bisect.bisect_right(self.log, x) - 1
        return self.fis[i] + (x - self.log[i])

    def salto(self, desde_log, delta):
        """A partir de desde_log los datos están delta bytes más adelante."""
        desde_log = max(desde_log, self.log[-1])
        nueva = self.fisica(desde_log) + delta
        if desde_log == self.log[-1]:
            self.fis[-1] = nueva
        else:
            self.log.append(desde_log)
            self.fis.append(nueva)

    def tramos(self, ini, fin):
        """Trozos físicos (pos, largo) que cubren [ini, fin) lógico."""
        res, x = [], ini
        while x < fin:
            i = bisect.bisect_right(self.log, x) - 1
            sig = self.log[i + 1] if i + 1 < len(self.log) else fin
            hasta = min(fin, sig)
            res.append((self.fis[i] + (x - self.log[i]), hasta - x))
            x = hasta
        return res


def leer_logico(f, mapa, x, n):
    return b"".join(leer(f, pos, largo) for pos, largo in mapa.tramos(x, x + n))


def fotograma_valido_mapa(f, mapa, off, tam):
    """Igual que fotograma_valido, pero siguiendo el mapa de trozos (el fotograma
    puede estar partido por un salto)."""
    x = 0
    while x < tam:
        cab = leer_logico(f, mapa, off + x, 5)
        if len(cab) < 5:
            return False
        largo = struct.unpack(">I", cab[:4])[0]
        if largo == 0 or x + 4 + largo > tam or cab[4] & 0x80:
            return False
        x += 4 + largo
    return x == tam


# ------------------------------------------------------------------ pasos

def buscar(f, total):
    fotos, indices, inicios = array("Q"), [], []
    p_aud = re.compile(rb"\x00\x00\x00\x02\x09")
    p_ftyp = re.compile(rb"ftyp(?:XAVC|MSNV|mp4[12]|iso[m2-6]|avc1)")
    p_moov = re.compile(rb"moov....mvhd", re.S)
    t0 = time.time()
    for base in range(0, total, BLOQUE):
        buf = leer(f, base, BLOQUE + 16)
        for m in p_aud.finditer(buf):
            if m.start() < BLOQUE:
                fotos.append(base + m.start())
        for m in p_ftyp.finditer(buf):
            if 4 <= m.start() < BLOQUE:
                inicios.append(base + m.start() - 4)
        for m in p_moov.finditer(buf):
            if 4 <= m.start() < BLOQUE:
                indices.append(base + m.start() - 4)
        hecho = min(total, base + BLOQUE)
        vel = hecho / MB / max(0.001, time.time() - t0)
        print(f"\rBuscando: {hecho * 100 // total:3d}%  ({vel:.0f} MB/s)  fotogramas: {len(fotos)}  "
              f"índices: {len(indices)}", end="", flush=True)
    print()
    return fotos, inicios, indices


def localizar_inicio(f, por_resto, m):
    """Encuentra dónde empezaría el video en la tarjeta a partir de su 2.º fotograma
    (el 1.º puede llevar cabeceras extra). Como los videos empiezan al inicio de un
    bloque de 128 KB, el fotograma debe estar en la misma posición dentro de su bloque."""
    k = 1 if len(m) > 2 else 0
    off, tam = m[k]
    mejor, puntos = None, -1
    for p in por_resto.get(off % CL, ()):
        base = p - off
        if not fotograma_valido(f, p, tam):
            continue
        ok = sum(fotograma_valido(f, base + o, s) for o, s in m[k:k + 8])
        if ok > puntos:
            mejor, puntos = base, ok
            if ok == len(m[k:k + 8]):
                break
    return mejor


def seguir_video(f, fotos, m, base, ini_datos):
    """Sigue el video fotograma a fotograma y arma el mapa de trozos.
    Devuelve (mapa, fotogramas perdidos)."""
    mapa = Mapa(ini_datos, base + ini_datos)
    perdidos = 0
    anterior = None  # (offset, tamaño) del último fotograma correcto
    for off, tam in m:
        pos = mapa.fisica(off)
        if fotograma_valido(f, pos, tam):
            anterior = (off, tam)
            continue
        # salto: buscar este fotograma más adelante, a un múltiplo de 128 KB
        i = bisect.bisect_right(fotos, pos)
        hallado = None
        while i < len(fotos) and fotos[i] <= pos + VENTANA:
            q = fotos[i]
            if (q - pos) % CL == 0 and fotograma_valido(f, q, tam):
                hallado = q
                break
            i += 1
        if hallado is None:
            perdidos += 1
            continue
        delta = hallado - pos
        # ¿en qué límite de bloque empieza el salto? Puede caer dentro del fotograma
        # anterior: se prueban los límites entre el inicio de ese fotograma y este,
        # y se elige el último con el que ambos fotogramas quedan correctos.
        desde_min = anterior[0] if anterior else mapa.log[-1]
        candidatos = []
        x = off
        while x > desde_min:
            fis = mapa.fisica(x)
            x_lim = x - (fis % CL)  # límite de bloque en o antes de x
            if x_lim <= desde_min:
                break
            candidatos.append(x_lim)
            x = x_lim - 1
        if not candidatos or candidatos[0] != off and mapa.fisica(off) % CL == 0:
            candidatos.insert(0, off)
        elegido = candidatos[-1] if candidatos else off
        for x_lim in candidatos:
            prueba = mapa.copia()
            prueba.salto(x_lim, delta)
            if (fotograma_valido_mapa(f, prueba, off, tam) and
                    (anterior is None or fotograma_valido_mapa(f, prueba, *anterior))):
                elegido = x_lim
                break
        mapa.salto(elegido, delta)
        anterior = (off, tam)
    return mapa, perdidos


def cabecera_real(f, inicios, base, ini_datos):
    """Cabecera original (ftyp, uuid, free, mdat) del video, si está cerca."""
    i = bisect.bisect_right(inicios, base) - 1
    while i >= 0 and base - inicios[i] <= 8 * MB:
        cab = leer(f, inicios[i], ini_datos)
        p = 0
        while p + 16 <= len(cab):
            tam, tipo = struct.unpack(">I4s", cab[p:p + 8])
            if tipo == b"mdat":
                datos = p + (16 if tam == 1 else 8)
                if datos == ini_datos:
                    return cab[:p], datos - p
                break
            if tam < 8:
                break
            p += tam
        i -= 1
    return None, 0


def cabecera_generica(ini_datos):
    ftyp = struct.pack(">I4s4sI", 28, b"ftyp", b"XAVC", 0x100) + b"XAVCmp42iso2"
    relleno = ini_datos - len(ftyp) - 16
    if relleno < 8:
        return None, 0
    return ftyp + struct.pack(">I4s", relleno, b"free") + bytes(relleno - 8), 16


def guardar(f, ruta, cab, largo_cab_mdat, mapa, info, moov, extra):
    ini, fin = info["ini_datos"], info["fin_datos"]
    with open(ruta, "wb") as out:
        out.write(cab)
        tam_mdat = fin - ini + largo_cab_mdat
        if largo_cab_mdat == 16:
            out.write(struct.pack(">I4sQ", 1, b"mdat", tam_mdat))
        else:
            out.write(struct.pack(">I4s", tam_mdat, b"mdat"))
        for pos, largo in mapa.tramos(ini, fin):
            while largo > 0:
                n = min(16 * MB, largo)
                out.write(leer(f, pos, n))
                pos += n
                largo -= n
        out.write(moov)
        out.write(extra)


def nombre_libre(carpeta, base):
    nombre, k = f"{base}.MP4", 2
    while os.path.exists(os.path.join(carpeta, nombre)):
        nombre = f"{base}_{k}.MP4"
        k += 1
    return os.path.join(carpeta, nombre)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    img, salida = sys.argv[1], sys.argv[2]
    os.makedirs(salida, exist_ok=True)
    total = os.path.getsize(img)
    f = open(img, "rb")

    print("PASO 1/2: buscando fotogramas e índices en la tarjeta...")
    fotos, inicios, pos_indices = buscar(f, total)
    if not fotos or not pos_indices:
        sys.exit("No se encontraron videos Sony en la imagen.")
    por_resto = {}
    for p in fotos:
        por_resto.setdefault(p % CL, []).append(p)

    print("PASO 2/2: reconstruyendo cada video...")
    ok = incompletos = proxys = sin_video = 0
    t0 = time.time()
    for n, pm in enumerate(pos_indices, 1):
        print(f"\r  video {n}/{len(pos_indices)}  completos: {ok}  con fallos: {incompletos}  "
              f"proxy: {proxys}  no encontrados: {sin_video}   ", end="", flush=True)
        tam = struct.unpack(">I", leer(f, pm, 4))[0]
        if not 8 < tam < 64 * MB:
            continue
        moov = leer(f, pm, tam)
        try:
            info = analizar_moov(moov)
        except (struct.error, IndexError, ValueError):
            continue
        v = info["video"]
        if not v or info["ini_datos"] is None:
            continue
        m = v["muestras"]
        base = localizar_inicio(f, por_resto, m)
        if base is None:
            sin_video += 1
            continue
        mapa, perdidos = seguir_video(f, fotos, m, base, info["ini_datos"])

        cab, largo = cabecera_real(f, inicios, base, info["ini_datos"])
        if cab is None:
            cab, largo = cabecera_generica(info["ini_datos"])
            if cab is None:
                sin_video += 1
                continue
        extra = b""
        sig = leer(f, pm + tam, 8)
        if len(sig) == 8 and sig[4:8] == b"meta":
            t_meta = struct.unpack(">I", sig[:4])[0]
            if 8 < t_meta < 4 * MB:
                extra = leer(f, pm + tam, t_meta)

        es_proxy = v.get("ancho", 9999) < 1920 and v.get("ancho", 0) > 0
        fecha = info["fecha"].strftime("%Y-%m-%d_%H-%M-%S") if info["fecha"] else f"video_{n:04d}"
        if es_proxy:
            carpeta = os.path.join(salida, "proxy_baja_calidad")
            proxys += 1
        elif perdidos:
            carpeta = os.path.join(salida, "con_fallos")
            incompletos += 1
        else:
            carpeta = salida
            ok += 1
        os.makedirs(carpeta, exist_ok=True)
        guardar(f, nombre_libre(carpeta, f"Sony_{fecha}"), cab, largo, mapa, info, moov, extra)

    print(f"\r  video {len(pos_indices)}/{len(pos_indices)}  ({time.time() - t0:.0f} s)" + " " * 40)
    print("\nResultado:")
    print(f"  Videos completos:          {ok}")
    print(f"  Videos con algún fallo:    {incompletos}  (carpeta con_fallos: se ven, con algún salto)")
    print(f"  Copias proxy (baja calidad): {proxys}  (carpeta proxy_baja_calidad)")
    print(f"  No encontrados:            {sin_video}  (sobrescritos)")
    print(f"Guardados en: {os.path.abspath(salida)}")


if __name__ == "__main__":
    main()
