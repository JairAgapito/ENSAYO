"""Prueba del reparador con una tarjeta simulada como las Sony reales:
- tras la cabecera de cada video hay 256 KB de datos ajenos,
- el índice (moov) va después de los datos,
- un video tiene además otro salto de datos ajenos en medio.
Comprueba que la imagen de los videos reparados es idéntica a la original.
Requiere ffmpeg con libx264."""
import os, struct, subprocess, sys, tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
REPARADOR = os.path.join(os.path.dirname(AQUI), "reparar_videos_sony.py")
CL = 128 * 1024


def cajas(d, ini, fin):
    p = ini
    while p + 8 <= fin:
        tam, tipo = struct.unpack(">I4s", d[p:p + 8]); cab = 8
        if tam == 1:
            tam = struct.unpack(">Q", d[p + 8:p + 16])[0]; cab = 16
        yield tipo, p, p + cab, p + tam
        p += tam


def desplazar(moov, delta):
    moov = bytearray(moov)
    def rec(ini, fin):
        for tipo, p, a, b in cajas(moov, ini, fin):
            if tipo in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                rec(a, b)
            elif tipo == b"stco":
                n = struct.unpack(">I", moov[a + 4:a + 8])[0]
                for k in range(n):
                    q = a + 8 + 4 * k
                    moov[q:q + 4] = struct.pack(">I", struct.unpack(">I", moov[q:q + 4])[0] + delta)
    rec(0, len(moov))
    return bytes(moov)


def clip_sony(t, nombre, dur, dia, fuente, audio):
    f = os.path.join(t, nombre)
    a = ["-f", "lavfi", "-i", f"sine=d={dur}", "-c:a", "aac"] if audio else ["-an"]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{fuente}=d={dur}:s=1920x1080:r=30", *a[:4], "-vf", "noise=alls=30:allf=t",
                    "-c:v", "libx264", "-x264-params", "aud=1:slices=8", "-g", "15", *a[4:],
                    "-metadata", f"creation_time=2026-10-{dia:02d}T10:00:00Z", f], check=True)
    d = open(f, "rb").read()
    partes = {tipo: (p, a_, b) for tipo, p, a_, b in cajas(d, 0, len(d))}
    _, ini, fin = partes[b"mdat"]; datos = d[ini:fin]
    p, _, b2 = partes[b"moov"]; moov = desplazar(d[p:b2], CL - ini)
    ftyp = struct.pack(">I4s4sI", 28, b"ftyp", b"XAVC", 0x100) + b"XAVCmp42iso2"
    uuid = struct.pack(">I4s", 148, b"uuid") + os.urandom(140)
    free = struct.pack(">I4s", CL - 16 - 28 - 148, b"free") + bytes(CL - 16 - 28 - 148 - 8)
    cab = ftyp + uuid + free + struct.pack(">I4sQ", 1, b"mdat", 16 + len(datos))
    meta = struct.pack(">I4s", 64, b"meta") + bytes(56)
    return f, cab, datos, moov + meta


def main():
    al = lambda b: b + os.urandom(-len(b) % CL)
    with tempfile.TemporaryDirectory() as t:
        o1, h1, d1, m1 = clip_sony(t, "a.mp4", 2, 1, "testsrc", False)
        o2, h2, d2, m2 = clip_sony(t, "b.mp4", 2, 2, "testsrc2", False)
        o3, h3, d3, m3 = clip_sony(t, "c.mp4", 2, 3, "rgbtestsrc", True)
        img = bytearray(os.urandom(3 * CL))
        for h, d, m, salto in ((h1, d1, m1, False), (h2, d2, m2, True), (h3, d3, m3, True)):
            img += h + os.urandom(2 * CL)                  # cabecera + 256 KB ajenos
            if salto:                                      # otro trozo ajeno en medio
                corte = (len(d) // 2) // CL * CL
                img += d[:corte] + os.urandom(2 * CL) + al(d[corte:])
            else:
                img += al(d)
            img += al(m) + os.urandom(CL)
        ruta = os.path.join(t, "tarjeta.img"); open(ruta, "wb").write(img)
        sal = os.path.join(t, "out")
        subprocess.run([sys.executable, REPARADOR, ruta, sal], check=True)
        buenos = sorted(n for n in os.listdir(sal) if n.endswith(".MP4"))
        print(buenos)
        md5 = lambda f: subprocess.run(["ffmpeg", "-loglevel", "error", "-i", f, "-map", "0:v",
                                        "-f", "framemd5", "-"], capture_output=True, text=True).stdout
        assert len(buenos) == 3, buenos
        for nombre, orig in zip(buenos, (o1, o2, o3)):
            assert md5(os.path.join(sal, nombre)) == md5(orig), f"{nombre} no coincide"
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", os.path.join(sal, buenos[2]),
                            "-f", "null", "-"], capture_output=True, text=True)
        assert not r.stderr.strip(), r.stderr
    print("OK: 3 videos reparados (con saltos) idénticos al original")


if __name__ == "__main__":
    main()
