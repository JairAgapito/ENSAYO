"""Prueba: mete archivos reales en una tarjeta simulada (con ruido entre medio),
ejecuta la recuperación y compara que los archivos salgan idénticos.
Requiere Pillow; ffmpeg es opcional (para probar MP4/MP3/WAV)."""
import hashlib, io, os, random, shutil, subprocess, sys, tempfile, zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import recuperar_sd  # noqa: E402

from PIL import Image  # noqa: E402


def foto(fmt, **kw):
    img = Image.effect_noise((400, 300), 60).convert("RGB")
    b = io.BytesIO(); img.save(b, fmt, **kw); return b.getvalue()


def docx():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document>" + os.urandom(4000).hex() + "</w:document>")
    return b.getvalue()


def pdf():
    cuerpo = b"%PDF-1.4\n1 0 obj<<>>endobj\n" + b"% relleno\n" * 300
    return cuerpo + b"trailer<<>>\n%%EOF\n" + b"2 0 obj<<>>endobj\n%%EOF\n"


def ffmpeg(args, ext):
    if not shutil.which("ffmpeg"):
        return None
    with tempfile.TemporaryDirectory() as t:
        f = os.path.join(t, "x." + ext)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, f], check=True)
        return open(f, "rb").read()


def main():
    rnd = random.Random(1)
    archivos = {
        "jpg": foto("JPEG", quality=90),
        "jpg2": foto("JPEG", quality=80, progressive=True),
        "png": foto("PNG"),
        "gif": foto("GIF"),
        "webp": foto("WEBP"),
        "pdf": pdf(),
        "docx": docx(),
        "mp4": ffmpeg(["-f", "lavfi", "-i", "testsrc=d=2:s=320x240", "-pix_fmt", "yuv420p"], "mp4"),
        "mov": ffmpeg(["-f", "lavfi", "-i", "testsrc=d=1:s=160x120"], "mov"),
        "mp3": ffmpeg(["-f", "lavfi", "-i", "sine=d=3", "-id3v2_version", "3"], "mp3"),
        "wav": ffmpeg(["-f", "lavfi", "-i", "sine=d=1"], "wav"),
    }
    archivos = {k: v for k, v in archivos.items() if v}

    with tempfile.TemporaryDirectory() as t:
        img = os.path.join(t, "tarjeta.img")
        with open(img, "wb") as f:
            for datos in archivos.values():
                f.write(bytes(rnd.getrandbits(8) for _ in range(rnd.randint(1, 8) * 512)))
                f.write(datos)
                f.write(b"\x00" * (-f.tell() % 512))
            f.write(os.urandom(256 * 1024))

        # probar también el comando de copia de imagen
        copia = os.path.join(t, "copia.img")
        recuperar_sd.crear_imagen(img, copia)
        assert open(copia, "rb").read() == open(img, "rb").read()

        salida = os.path.join(t, "out")
        recuperar_sd.recuperar(copia, salida)
        recuperados = set()
        for raiz, _, nombres in os.walk(salida):
            for n in nombres:
                recuperados.add(hashlib.sha256(open(os.path.join(raiz, n), "rb").read()).hexdigest())

    fallos = [k for k, v in archivos.items() if hashlib.sha256(v).hexdigest() not in recuperados]
    print(f"\nOriginales: {len(archivos)}  Recuperados idénticos: {len(archivos) - len(fallos)}")
    if fallos:
        print("FALLARON:", fallos); sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
