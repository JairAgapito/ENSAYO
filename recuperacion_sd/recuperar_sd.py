#!/usr/bin/env python3
"""
Recuperador de fotos y videos borrados de memorias SD de cámaras Sony
(también sirve si la tarjeta se formateó desde la cámara). Técnica: file carving.

Cuando se borra un archivo, normalmente solo se borra su entrada en la tabla
del sistema de archivos; los datos siguen en la tarjeta hasta que algo los
sobrescribe. Este programa lee la tarjeta byte a byte y busca las "firmas"
con que empieza cada tipo de archivo (JPEG, PNG, MP4, PDF, DOCX...), calcula
dónde termina cada uno y lo guarda en una carpeta.

Solo usa la biblioteca estándar de Python 3.8+.

Uso:
  1) Crear una imagen de la tarjeta (recomendado, nunca escribe en la SD):
       python recuperar_sd.py imagen  ORIGEN  tarjeta.img
     ORIGEN en Windows:  \\\\.\\E:      (letra de la SD, ejecutar como Administrador)
     ORIGEN en Linux:    /dev/sdb      (usar sudo; ver con `lsblk`)
     ORIGEN en macOS:    /dev/rdisk4   (usar sudo; ver con `diskutil list external`
                                        y antes `diskutil unmountDisk /dev/disk4`)
     En Mac también puedes usar el asistente: bash recuperar_mac.command

  2) Recuperar archivos desde la imagen:
       python recuperar_sd.py recuperar tarjeta.img --salida recuperados
       python recuperar_sd.py recuperar tarjeta.img --salida fotos --tipos jpg,png,heic
"""

import argparse
import mmap
import os
import re
import struct
import sys
import time

MB = 1024 * 1024
SECTOR = 512

# --------------------------------------------------------------------------
# Paso 1: copiar la tarjeta a un archivo imagen (solo lectura sobre la SD)
# --------------------------------------------------------------------------


def crear_imagen(origen, destino, bloque=4 * MB):
    """Copia ORIGEN a DESTINO. Si hay sectores dañados, los rellena con ceros
    y sigue, en lugar de abortar."""
    if os.path.abspath(origen) == os.path.abspath(destino):
        sys.exit("El destino no puede ser el mismo que el origen.")

    fd = os.open(origen, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    total = _tamano_dispositivo(fd)
    malos = 0
    copiado = 0
    inicio = time.time()

    with open(destino, "wb") as out:
        while True:
            try:
                os.lseek(fd, copiado, os.SEEK_SET)
                datos = os.read(fd, bloque)
            except OSError:
                datos, m = _leer_por_sectores(fd, copiado, bloque)
                malos += m
                if datos is None:  # fin del dispositivo
                    break
            if not datos:
                break
            out.write(datos)
            copiado += len(datos)
            _progreso("Copiando", copiado, total, inicio)

    os.close(fd)
    print(f"\nImagen creada: {destino} ({copiado / MB:.1f} MB)")
    if malos:
        print(f"Aviso: {malos} sectores ilegibles se rellenaron con ceros.")


def _leer_por_sectores(fd, pos, bloque):
    """Reintenta un bloque fallido sector por sector."""
    partes, malos = [], 0
    for off in range(pos, pos + bloque, SECTOR):
        try:
            os.lseek(fd, off, os.SEEK_SET)
            s = os.read(fd, SECTOR)
        except OSError:
            s = b"\x00" * SECTOR
            malos += 1
        if not s:
            break
        partes.append(s)
    if not partes:
        return None, malos
    if malos == len(partes):
        # Todo el bloque falló: puede ser el final del dispositivo en Windows.
        try:
            os.lseek(fd, pos + bloque, os.SEEK_SET)
            os.read(fd, SECTOR)
        except OSError:
            return None, 0
    return b"".join(partes), malos


def _tamano_dispositivo(fd):
    if sys.platform == "darwin":  # en macOS /dev/rdiskN no informa su tamaño con lseek
        try:
            import fcntl
            bs = struct.unpack("I", fcntl.ioctl(fd, 0x40046418, b"\0" * 4))[0]  # DKIOCGETBLOCKSIZE
            nb = struct.unpack("Q", fcntl.ioctl(fd, 0x40086419, b"\0" * 8))[0]  # DKIOCGETBLOCKCOUNT
            if bs and nb:
                return bs * nb
        except (OSError, ImportError):
            pass
    try:
        return os.lseek(fd, 0, os.SEEK_END) or None
    except OSError:
        return None
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass


def _progreso(etiqueta, hecho, total, inicio):
    vel = hecho / MB / max(time.time() - inicio, 0.001)
    if total:
        txt = f"{etiqueta}: {hecho * 100 / total:5.1f}%  ({hecho / MB:.0f}/{total / MB:.0f} MB, {vel:.1f} MB/s)"
    else:
        txt = f"{etiqueta}: {hecho / MB:.0f} MB ({vel:.1f} MB/s)"
    print("\r" + txt, end="", flush=True)


# --------------------------------------------------------------------------
# Paso 2: buscar archivos por firma y calcular dónde terminan
# Cada función recibe (datos, inicio) y devuelve (fin, extensión) o None.
# --------------------------------------------------------------------------

MAX = {
    "jpg": 60 * MB, "png": 60 * MB, "gif": 30 * MB, "pdf": 300 * MB,
    "zip": 500 * MB, "mp4": 64 * 1024 * MB, "riff": 4 * 1024 * MB, "mp3": 50 * MB,
    "raw": 300 * MB, "ts": 64 * 1024 * MB,
}


def fin_jpeg(d, i):
    n = len(d)
    p = i + 2
    limite = min(n, i + MAX["jpg"])
    while p + 4 <= limite:
        if d[p] != 0xFF:
            return None
        marca = d[p + 1]
        if marca == 0xFF:  # relleno
            p += 1
            continue
        if marca == 0xD9:  # EOI: fin de imagen
            return p + 2, "jpg"
        if 0xD0 <= marca <= 0xD7 or marca == 0x01:
            p += 2
            continue
        largo = struct.unpack(">H", d[p + 2:p + 4])[0]
        if largo < 2:
            return None
        p += 2 + largo
        if marca == 0xDA:  # SOS: datos comprimidos hasta el siguiente marcador
            while p + 1 < limite:
                q = d.find(b"\xff", p, limite)
                if q < 0 or q + 1 >= limite:
                    return None
                sig = d[q + 1]
                if sig == 0x00 or 0xD0 <= sig <= 0xD7 or sig == 0xFF:
                    p = q + 1 if sig == 0xFF else q + 2
                    continue
                p = q
                break
    return None


def fin_png(d, i):
    p = i + 8
    limite = min(len(d), i + MAX["png"])
    while p + 12 <= limite:
        largo = struct.unpack(">I", d[p:p + 4])[0]
        tipo = d[p + 4:p + 8]
        if not tipo.isalpha():
            return None
        p += 12 + largo
        if tipo == b"IEND":
            return (p, "png") if p <= len(d) else None
    return None


def _saltar_subbloques(d, p, limite):
    while p < limite:
        largo = d[p]
        p += 1 + largo
        if largo == 0:
            return p
    return None


def fin_gif(d, i):
    limite = min(len(d), i + MAX["gif"])
    p = i + 13
    if d[i + 10] & 0x80:  # tabla de colores global
        p += 3 * (2 << (d[i + 10] & 7))
    while p is not None and p < limite:
        b = d[p]
        if b == 0x3B:  # trailer: fin del GIF
            return p + 1, "gif"
        if b == 0x21:  # extensión
            p = _saltar_subbloques(d, p + 2, limite)
        elif b == 0x2C:  # imagen
            empaq = d[p + 9]
            p += 10
            if empaq & 0x80:
                p += 3 * (2 << (empaq & 7))
            p = _saltar_subbloques(d, p + 1, limite)
        else:
            return None
    return None


def fin_pdf(d, i):
    limite = min(len(d), i + MAX["pdf"])
    siguiente = d.find(b"%PDF-", i + 5, limite)
    if siguiente > 0:
        limite = siguiente
    fin, p = None, i
    while True:  # el último %%EOF cubre las ediciones incrementales
        q = d.find(b"%%EOF", p, limite)
        if q < 0:
            break
        fin = q + 5
        p = fin
    if fin is None:
        return None
    while fin < limite and d[fin] in (0x0D, 0x0A):
        fin += 1
    return fin, "pdf"


def fin_zip(d, i):
    limite = min(len(d), i + MAX["zip"])
    q = d.find(b"PK\x05\x06", i, limite)
    if q < 0 or q + 22 > len(d):
        return None
    comentario = struct.unpack("<H", d[q + 20:q + 22])[0]
    cabeza = bytes(d[i:min(i + 64 * 1024, q)])
    if b"word/" in cabeza:
        ext = "docx"
    elif b"xl/" in cabeza:
        ext = "xlsx"
    elif b"ppt/" in cabeza:
        ext = "pptx"
    elif b"AndroidManifest.xml" in cabeza:
        ext = "apk"
    else:
        ext = "zip"
    return q + 22 + comentario, ext


CAJAS_MP4 = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"uuid",
             b"meta", b"pnot", b"udta", b"mfra", b"moof", b"sidx", b"styp",
             b"pdin", b"prft", b"emsg", b"junk", b"iinf", b"iloc", b"idat"}


def fin_mp4(d, i):
    """i apunta al tamaño de la caja 'ftyp' (4 bytes antes del texto)."""
    marca = bytes(d[i + 8:i + 12])
    if marca.startswith(b"qt"):
        ext = "mov"
    elif marca.startswith(b"3g"):
        ext = "3gp"
    elif marca in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        ext = "heic"
    elif marca == b"avif":
        ext = "avif"
    elif marca in (b"M4A ", b"M4B "):
        ext = "m4a"
    elif marca == b"crx ":
        ext = "cr3"  # RAW de Canon (EOS R, M50, 90D...)
    else:
        ext = "mp4"
    n = len(d)
    limite = min(n, i + MAX["mp4"])
    p, vio_datos = i, False
    while p + 8 <= limite:
        tam = struct.unpack(">I", d[p:p + 4])[0]
        tipo = bytes(d[p + 4:p + 8])
        if tipo not in CAJAS_MP4:
            break
        if tam == 1:
            if p + 16 > n:
                return None
            tam = struct.unpack(">Q", d[p + 8:p + 16])[0]
        elif tam == 0:
            return None  # "hasta el final": no se puede delimitar
        if tam < 8:
            break
        if tipo in (b"moov", b"mdat", b"meta", b"moof"):
            vio_datos = True
        p += tam
    if not vio_datos or p > n:
        return None
    return p, ext


def fin_riff(d, i):
    tam = struct.unpack("<I", d[i + 4:i + 8])[0]
    forma = bytes(d[i + 8:i + 12])
    ext = {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}.get(forma)
    if ext is None or tam < 4 or tam > MAX["riff"]:
        return None
    fin = i + 8 + tam + (tam & 1)
    return (fin, ext) if fin <= len(d) else None


_BITRATES = {  # kbps para MPEG-1 y MPEG-2/2.5, Layer III
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
_MUESTREO = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def fin_mp3(d, i):
    n = len(d)
    tam = d[i + 6] << 21 | d[i + 7] << 14 | d[i + 8] << 7 | d[i + 9]
    p = i + 10 + tam
    limite = min(n, i + MAX["mp3"])
    tramas = 0
    while p + 4 <= limite:
        if d[p] != 0xFF or (d[p + 1] & 0xE0) != 0xE0:
            break
        version = (d[p + 1] >> 3) & 3
        capa = (d[p + 1] >> 1) & 3
        if version == 1 or capa != 1:
            break
        br = _BITRATES[1 if version == 3 else 2][d[p + 2] >> 4] * 1000
        idx = (d[p + 2] >> 2) & 3
        if br == 0 or idx == 3:
            break
        sr = _MUESTREO[version][idx]
        relleno = (d[p + 2] >> 1) & 1
        largo = (144 if version == 3 else 72) * br // sr + relleno
        p += largo
        tramas += 1
    if tramas < 10:
        return None
    if p + 128 <= n and d[p:p + 3] == b"TAG":  # etiqueta ID3v1 al final
        p += 128
    return min(p, n), "mp3"


# ---- Fotos RAW de cámara basadas en TIFF: CR2, NEF, ARW, DNG, ORF, RW2, PEF...
_TAM_TIPO = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
             11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
_TAGS_IFD = (330, 34665, 34853, 40965)  # SubIFDs, EXIF, GPS, Interoperabilidad
_CABECERAS_ALINEADAS = re.compile(
    rb"\xff\xd8\xff|II\*\x00|MM\x00\*|IIU\x00|IIRO|FUJIFILM|....ftyp|RIFF|\x89PNG", re.S)


def _siguiente_archivo(d, desde, limite):
    """Primer encabezado de archivo que empiece al inicio de un sector.
    En FAT/exFAT cada archivo empieza en un bloque nuevo, así que un encabezado
    alineado a 512 bytes casi seguro es el siguiente archivo."""
    desde += -desde % SECTOR
    for m in _CABECERAS_ALINEADAS.finditer(d, desde, limite):
        if m.start() % SECTOR == 0:
            return m.start()
    return limite


def fin_tiff(d, i):
    n = len(d)
    bo = "<" if d[i] == 0x49 else ">"
    magia = struct.unpack(bo + "H", d[i + 2:i + 4])[0]
    if magia not in (42, 0x4F52, 0x5352, 0x55):
        return None
    limite = min(n, i + MAX["raw"])
    fin = i + 8
    pendientes = [struct.unpack(bo + "I", d[i + 4:i + 8])[0]]
    vistos, valores, marca, dng = set(), {}, b"", False
    sin_largo = None  # inicio de datos crudos de tamaño desconocido (RW2)

    def leer(pos, tipo, cuenta):
        f = {3: "H", 4: "I", 13: "I"}.get(tipo)
        if not f or cuenta > 100000:
            return []
        if _TAM_TIPO[tipo] * cuenta > 4:
            pos = i + struct.unpack(bo + "I", d[pos:pos + 4])[0]
        return list(struct.unpack(bo + f * cuenta, d[pos:pos + _TAM_TIPO[tipo] * cuenta]))

    while pendientes and len(vistos) < 64:
        off = pendientes.pop()
        if off < 8 or off in vistos or i + off + 2 > limite:
            continue
        vistos.add(off)
        p = i + off
        num = struct.unpack(bo + "H", d[p:p + 2])[0]
        if not 1 <= num <= 1000:
            if len(vistos) == 1:
                return None
            continue
        fin = max(fin, p + 2 + 12 * num + 4)
        malos = 0
        tiras = {}
        for k in range(num):
            e = p + 2 + 12 * k
            tag, tipo, cuenta = struct.unpack(bo + "HHI", d[e:e + 8])
            tam = _TAM_TIPO.get(tipo)
            if not tam:
                malos += 1
                continue
            total = tam * cuenta
            if total > 4:
                voff = struct.unpack(bo + "I", d[e + 8:e + 12])[0]
                if i + voff + total <= limite:
                    fin = max(fin, i + voff + total)
            if tag == 271 and tipo == 2:  # fabricante
                v = d[e + 8:e + 12] if total <= 4 else d[i + struct.unpack(bo + "I", d[e + 8:e + 12])[0]:][:total]
                marca = bytes(v).upper()
            elif tag == 50706:
                dng = True
            elif tag in (273, 279, 324, 325, 513, 514, 280) or tag in _TAGS_IFD:
                tiras[tag] = leer(e + 8, tipo, cuenta)
        if malos > num // 2:
            if len(vistos) == 1:
                return None
            continue
        for a, b in ((273, 279), (324, 325), (513, 514)):
            for o, c in zip(tiras.get(a, []), tiras.get(b, [])):
                if i + o + c <= limite:
                    fin = max(fin, i + o + c)
        if 273 in tiras and 279 not in tiras and tiras[273]:
            sin_largo = i + tiras[273][0]
        if 280 in tiras and tiras[280]:  # Panasonic RawDataOffset
            sin_largo = i + tiras[280][0]
        for t in _TAGS_IFD:
            pendientes.extend(tiras.get(t, []))
        sig = struct.unpack(bo + "I", d[p + 2 + 12 * num:p + 6 + 12 * num])[0]
        if sig:
            pendientes.append(sig)

    if sin_largo is not None and sin_largo < limite:
        fin = max(fin, _siguiente_archivo(d, sin_largo + 1, limite))
    if fin > n:
        return None

    if bytes(d[i + 8:i + 10]) == b"CR":
        ext = "cr2"
    elif magia in (0x4F52, 0x5352):
        ext = "orf"
    elif magia == 0x55:
        ext = "rw2"
    elif dng:
        ext = "dng"
    elif marca.startswith(b"NIKON"):
        ext = "nef"
    elif marca.startswith(b"SONY"):
        ext = "arw"
    elif marca.startswith(b"PENTAX") or marca.startswith(b"RICOH"):
        ext = "pef"
    elif marca.startswith(b"OLYMPUS") or marca.startswith(b"OM DIGITAL"):
        ext = "orf"
    elif marca.startswith(b"SAMSUNG"):
        ext = "srw"
    elif marca.startswith(b"CANON"):
        ext = "cr2"
    else:
        ext = "tif"
    return fin, ext


def fin_raf(d, i):
    """RAW de Fujifilm: la cabecera indica dónde están la vista previa y los datos."""
    vals = struct.unpack(">IIIIII", d[i + 84:i + 108])
    fin = i + 108
    for off, largo in zip(vals[0::2], vals[1::2]):
        if 0 < largo <= MAX["raw"] and off <= MAX["raw"]:
            fin = max(fin, i + off + largo)
    return (fin, "raf") if fin <= len(d) and fin > i + 4096 else None


def fin_ts(d, i):
    """Video AVCHD (.MTS/.M2TS de Sony, Panasonic, Canon) o MPEG-TS.
    i apunta al byte 0x47 de un paquete PAT."""
    n = len(d)
    for paso, adelanto in ((192, 4), (188, 0)):
        if all(i + k * paso < n and d[i + k * paso] == 0x47 for k in range(1, 6)):
            break
    else:
        return None
    ini = i - adelanto
    while ini - paso >= 0 and d[ini - paso + adelanto] == 0x47:  # retroceder al primer paquete
        ini -= paso
    p = ini
    limite = min(n, ini + MAX["ts"])
    while p + paso <= limite and d[p + adelanto] == 0x47:
        p += paso
    if p - ini < 50 * paso:
        return None
    return p, ("mts" if paso == 192 else "ts"), ini


_TIPOS_TIFF = {"cr2", "nef", "arw", "dng", "orf", "rw2", "pef", "srw", "tif"}

# Firma regex -> (función, desplazamiento de inicio, tipos que puede producir)
FIRMAS = [
    (rb"\xff\xd8\xff[\xc0-\xfe]", fin_jpeg, 0, {"jpg"}),
    (rb"\x89PNG\r\n\x1a\n", fin_png, 0, {"png"}),
    (rb"GIF8[79]a", fin_gif, 0, {"gif"}),
    (rb"%PDF-1\.", fin_pdf, 0, {"pdf"}),
    (rb"PK\x03\x04", fin_zip, 0, {"zip", "docx", "xlsx", "pptx", "apk"}),
    (rb"ftyp", fin_mp4, -4, {"mp4", "mov", "3gp", "heic", "avif", "m4a", "cr3"}),
    (rb"II\*\x00", fin_tiff, 0, _TIPOS_TIFF),
    (rb"MM\x00\*", fin_tiff, 0, _TIPOS_TIFF),
    (rb"IIU\x00", fin_tiff, 0, _TIPOS_TIFF),
    (rb"IIR[OS]", fin_tiff, 0, _TIPOS_TIFF),
    (rb"FUJIFILMCCD-RAW ", fin_raf, 0, {"raf"}),
    (rb"\x47\x40\x00[\x10-\x1f]\x00\x00\xb0", fin_ts, 0, {"mts", "ts"}),
    (rb"RIFF", fin_riff, 0, {"wav", "avi", "webp"}),
    (rb"ID3[\x02-\x04]\x00", fin_mp3, 0, {"mp3"}),
]
TODOS_LOS_TIPOS = sorted(set().union(*(f[3] for f in FIRMAS)))
# Formatos que graban las cámaras Sony (Alpha, ZV, RX, Handycam):
# fotos JPG/ARW/HIF y video XAVC S / XAVC HS (.MP4) o AVCHD (.MTS)
TIPOS_SONY = ["jpg", "arw", "heic", "mp4", "mts"]


def _coincidencias(fh, total, patrones, cubierto, ventana=64 * MB):
    """Recorre la imagen por bloques y entrega (posición, nº de firma) en orden.
    Lee cada bloque de una vez con read(): en discos duros mecánicos es mucho
    más rápido que dejar que mmap lea la imagen en trocitos.
    Salta los bloques que ya forman parte de un archivo recuperado (ej. un video)."""
    for base in range(0, total, ventana):
        tope = base + ventana
        if tope <= cubierto():
            continue
        desde = max(base, cubierto())
        fh.seek(desde)
        buf = fh.read(min(total, tope + 32) - desde)
        hallados = sorted(
            (m.start() + desde, k)
            for k, p in enumerate(patrones)
            for m in p.finditer(buf)
            if m.start() + desde < tope)
        del buf
        yield from hallados


def recuperar(origen, salida, tipos=None, minimo=1024):
    tipos = set(tipos) if tipos else set(TODOS_LOS_TIPOS)
    firmas = [f for f in FIRMAS if f[3] & tipos]
    # Una búsqueda por firma: unirlas en un solo regex lo hace ~50 veces más lento.
    patrones = [re.compile(f[0]) for f in firmas]

    with open(origen, "rb") as fh:
        try:
            datos = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError) as e:
            sys.exit(f"No se pudo abrir '{origen}' ({e}).\n"
                     "Crea primero una imagen con el comando 'imagen' y usa esa imagen.")

    os.makedirs(salida, exist_ok=True)
    total = len(datos)
    cubierto = 0  # evita guardar fotos que están dentro de un video ya recuperado, etc.
    conteo = {}
    inicio = time.time()
    ultimo = 0.0

    lector = open(origen, "rb", buffering=0)
    copiador = open(origen, "rb", buffering=0)
    for m_start, k in _coincidencias(lector, total, patrones, lambda: cubierto):
        func, desp, _ = firmas[k][1:]
        pos = m_start + desp
        if pos < cubierto or pos < 0:
            continue
        ahora = time.time()
        if ahora - ultimo > 0.5:
            _progreso("Analizando", pos, total, inicio)
            ultimo = ahora
        try:
            r = func(datos, pos)
        except (struct.error, IndexError):
            r = None
        if not r:
            continue
        fin, ext = r[0], r[1]
        if len(r) > 2:  # la función encontró un inicio anterior
            if r[2] < cubierto:
                continue
            pos = r[2]
        if ext not in tipos or fin - pos < minimo:
            continue
        num = conteo.get(ext, 0) + 1
        conteo[ext] = num
        carpeta = os.path.join(salida, ext)
        os.makedirs(carpeta, exist_ok=True)
        nombre = os.path.join(carpeta, f"{ext}_{num:05d}_off{pos:012x}.{ext}")
        with open(nombre, "wb") as out:
            copiador.seek(pos)
            for a in range(pos, fin, 16 * MB):
                out.write(copiador.read(min(fin, a + 16 * MB) - a))
                if time.time() - ultimo > 0.5:  # los videos grandes tardan en guardarse
                    _progreso("Analizando", a, total, inicio)
                    print(f"  guardando {os.path.basename(nombre)} ({(fin - pos) / MB:.0f} MB)   ",
                          end="", flush=True)
                    ultimo = time.time()
        cubierto = fin

    _progreso("Analizando", total, total, inicio)
    lector.close()
    copiador.close()
    datos.close()
    print("\n\nResultado:")
    if not conteo:
        print("  No se encontraron archivos.")
    for ext, n in sorted(conteo.items()):
        print(f"  {ext:5s} {n}")
    print(f"Archivos guardados en: {os.path.abspath(salida)}")
    return conteo


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recupera archivos borrados de memorias SD/USB.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("imagen", help="copiar la tarjeta completa a un archivo .img")
    a.add_argument("origen", help=r"dispositivo: \\.\E:  /dev/sdb  /dev/rdisk4")
    a.add_argument("destino", help="archivo .img (en OTRO disco, nunca en la SD)")

    b = sub.add_parser("recuperar", help="buscar archivos dentro de la imagen")
    b.add_argument("origen", help="archivo .img (o dispositivo en Linux/macOS)")
    b.add_argument("--salida", default="recuperados", help="carpeta destino")
    b.add_argument("--tipos", help="por defecto los de cámaras Sony (" + ",".join(TIPOS_SONY)
                   + "). Usa 'todos' para buscar todo. Disponibles: " + ",".join(TODOS_LOS_TIPOS))
    b.add_argument("--minimo", type=int, default=1024, help="tamaño mínimo en bytes")

    args = ap.parse_args(argv)
    if args.cmd == "imagen":
        crear_imagen(args.origen, args.destino)
    else:
        if not args.tipos:
            tipos = TIPOS_SONY
        elif args.tipos.strip().lower() == "todos":
            tipos = None
        else:
            alias = {"jpeg": "jpg", "hif": "heic", "heif": "heic", "m2ts": "mts"}
            tipos = [t.strip().lower().lstrip(".") for t in args.tipos.split(",")]
            tipos = [alias.get(t, t) for t in tipos]
            malos = set(tipos) - set(TODOS_LOS_TIPOS)
            if malos:
                sys.exit(f"Tipos no soportados: {', '.join(malos)}")
        recuperar(args.origen, args.salida, tipos, args.minimo)


if __name__ == "__main__":
    main()
