"""Simula una tarjeta SD de cámara Sony: graba fotos ARW + JPG y videos
XAVC S (.MP4) y AVCHD (.MTS), la FORMATEA (formato rápido, como hace la
cámara) y comprueba que el programa los recupera idénticos.
Requiere: Pillow, ffmpeg, mkfs.vfat (dosfstools) y mtools.
Las fotos ARW son sintéticas (misma estructura TIFF que un ARW real)."""
import hashlib, io, os, random, struct, subprocess, sys, tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import recuperar_sd  # noqa: E402
from PIL import Image  # noqa: E402


def jpg(w=640, h=480, seed=0):
    b = io.BytesIO()
    Image.effect_noise((w, h), 40 + seed).convert("RGB").save(b, "JPEG", quality=85)
    return b.getvalue()


def arw(seed, mosaicos=False):
    """ARW sintético: IFD0 (Make=SONY, vista previa JPEG, EXIF con MakerNote)
    + SubIFD con los datos RAW en tiras (o en mosaicos, como el ARW sin pérdida)."""
    rnd = random.Random(seed)
    preview = jpg(320, 240, seed)
    makernote = bytes(rnd.getrandbits(8) for _ in range(3000))
    raw = os.urandom(2 * 1024 * 1024 + seed * 1000)
    out = bytearray(b"II*\x00" + struct.pack("<I", 8))

    def ifd(entries, base):  # entries: (tag, tipo, cuenta, valor_bytes)
        n = len(entries)
        datos_off = base + 2 + 12 * n + 4
        cab, blobs = bytearray(struct.pack("<H", n)), bytearray()
        for tag, tipo, cuenta, val in sorted(entries):
            if len(val) <= 4:
                cab += struct.pack("<HHI", tag, tipo, cuenta) + val.ljust(4, b"\0")
            else:
                cab += struct.pack("<HHII", tag, tipo, cuenta, datos_off + len(blobs))
                blobs += val + b"\0" * (len(val) & 1)
        return bytes(cab + struct.pack("<I", 0) + blobs)

    L = lambda *v: struct.pack("<" + "I" * len(v), *v)
    # Posiciones fijas para simplificar: IFD0 en 8, EXIF en 2000, SubIFD en 6000,
    # vista previa en 8192, RAW después.
    pos_prev = 8192
    pos_raw = pos_prev + len(preview) + (-len(preview) % 512)
    ifd0 = ifd([(271, 2, 5, b"SONY\0"), (272, 2, 9, b"ILCE-7M4\0"),
                (513, 4, 1, L(pos_prev)), (514, 4, 1, L(len(preview))),
                (330, 4, 1, L(6000)), (34665, 4, 1, L(2000))], 8)
    exif = ifd([(37500, 7, len(makernote), makernote)], 2000)
    if mosaicos:
        t = len(raw) // 4
        offs = [pos_raw + k * t for k in range(4)]
        cnts = [t, t, t, len(raw) - 3 * t]
        sub = ifd([(256, 4, 1, L(7000)), (324, 4, 4, L(*offs)), (325, 4, 4, L(*cnts))], 6000)
    else:
        sub = ifd([(256, 4, 1, L(7000)), (273, 4, 1, L(pos_raw)), (279, 4, 1, L(len(raw)))], 6000)
    for pos, blk in ((8, ifd0), (2000, exif), (6000, sub), (pos_prev, preview), (pos_raw, raw)):
        assert len(out) <= pos, "bloques solapados"
        out += b"\0" * (pos - len(out)) + blk
    return bytes(out)


def ffmpeg(args, ext):
    with tempfile.TemporaryDirectory() as t:
        f = os.path.join(t, "x." + ext)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, f], check=True)
        return open(f, "rb").read()


def xavc():
    """MP4 con marca XAVC y caja 'uuid' antes de 'mdat', como graban las Sony."""
    d = ffmpeg(["-f", "lavfi", "-i", "testsrc=d=3:s=640x360", "-pix_fmt", "yuv420p",
                "-brand", "XAVC"], "mp4")
    n = struct.unpack(">I", d[:4])[0]
    uuid = struct.pack(">I", 8 + 16 + 64) + b"uuid" + os.urandom(16 + 64)
    return d[:n] + uuid + d[n:]


def main():
    archivos = {}
    for k in range(6):
        archivos[f"DSC{k:05d}.ARW"] = arw(k, mosaicos=(k % 2 == 1))
        archivos[f"DSC{k:05d}.JPG"] = jpg(1200, 800, k)
    archivos["C0001.MP4"] = xavc()
    archivos["00000.MTS"] = ffmpeg(["-f", "lavfi", "-i", "testsrc=d=3:s=640x360", "-pix_fmt",
                                    "yuv420p", "-f", "mpegts", "-mpegts_m2ts_mode", "1"], "mts")

    with tempfile.TemporaryDirectory() as t:
        img, orig = os.path.join(t, "sd.img"), os.path.join(t, "orig")
        os.makedirs(orig)
        for nombre, datos in archivos.items():
            open(os.path.join(orig, nombre), "wb").write(datos)
        subprocess.run(["truncate", "-s", "128M", img], check=True)
        mkfs = ["mkfs.vfat", "-F", "32", img]
        subprocess.run(mkfs, check=True, capture_output=True)
        env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
        subprocess.run(["mcopy", "-i", img] + [os.path.join(orig, n) for n in archivos] + ["::/"],
                       check=True, env=env)
        # La cámara "formatea": formato rápido (crea tabla nueva y vacía)
        subprocess.run(mkfs, check=True, capture_output=True)

        salida = os.path.join(t, "out")
        conteo = recuperar_sd.recuperar(img, salida, recuperar_sd.TIPOS_SONY)
        rec = {hashlib.sha256(open(os.path.join(a, f), "rb").read()).hexdigest()
               for a, _, fs in os.walk(salida) for f in fs}

    fallos = [n for n, d in archivos.items() if hashlib.sha256(d).hexdigest() not in rec]
    print(f"\nOriginales: {len(archivos)}  Recuperados idénticos: {len(archivos) - len(fallos)}")
    if fallos:
        print("FALLARON:", fallos)
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
