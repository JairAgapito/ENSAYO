"""Prueba del reparador: arma una tarjeta simulada como las Sony, con el índice
(moov) separado del video, y comprueba que los videos reparados son idénticos
fotograma a fotograma al original. Requiere ffmpeg."""
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


def clip_sony(t, nombre, dur, seg, fuente="testsrc"):
    f = os.path.join(t, nombre)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{fuente}=d={dur}:s=640x360:r=30", "-f", "lavfi", "-i", f"sine=d={dur}",
                    "-vf", "noise=alls=30:allf=t", "-c:v", "libx264", "-g", "15", "-c:a", "aac", "-metadata",
                    f"creation_time=2026-10-{seg:02d}T10:00:00Z", f], check=True)
    d = open(f, "rb").read()
    partes = {tipo: (p, a, b) for tipo, p, a, b in cajas(d, 0, len(d))}
    _, a, b = partes[b"mdat"]; datos = d[a:b]
    p, _, b2 = partes[b"moov"]; moov = desplazar(d[p:b2], CL - a)
    ftyp = struct.pack(">I4s4sI", 28, b"ftyp", b"XAVC", 0x100) + b"XAVCmp42iso2"
    uuid = struct.pack(">I4s", 148, b"uuid") + os.urandom(140)
    free = struct.pack(">I4s", CL - 16 - 28 - 148, b"free") + bytes(CL - 16 - 28 - 148 - 8)
    cab = ftyp + uuid + free + struct.pack(">I4sQ", 1, b"mdat", 16 + len(datos))
    meta = struct.pack(">I4s", 64, b"meta") + bytes(56)
    return f, cab + datos, moov + meta


def relleno(n):
    return os.urandom(n)


def main():
    with tempfile.TemporaryDirectory() as t:
        orig1, c1, m1 = clip_sony(t, "a.mp4", 3, 1)
        orig2, c2, m2 = clip_sony(t, "b.mp4", 4, 2, "testsrc2")
        orig3, c3, m3 = clip_sony(t, "c.mp4", 3, 3, "rgbtestsrc")
        al = lambda b: b + relleno(-len(b) % CL)
        img = bytearray(relleno(4 * CL))
        img += al(m1) + al(c1)                 # índice justo antes del video (como Sony)
        img += relleno(3 * CL) + al(c2)        # video 2 ...
        img += relleno(5 * CL) + al(m2)        # ... con su índice más adelante
        mitad = (len(c3) // 2) // CL * CL      # video 3 fragmentado: un bloque ajeno en medio
        img += al(m3) + c3[:mitad] + relleno(CL) + al(c3[mitad:])
        img += relleno(2 * CL)
        ruta = os.path.join(t, "tarjeta.img"); open(ruta, "wb").write(img)
        sal = os.path.join(t, "out")
        subprocess.run([sys.executable, REPARADOR, ruta, sal], check=True)
        hechos = sorted(os.listdir(sal)); print(hechos)
        rotos = os.listdir(os.path.join(sal, "posiblemente_incompletos"))
        md5 = lambda f: subprocess.run(["ffmpeg", "-loglevel", "error", "-i", f, "-map", "0",
                                        "-f", "framemd5", "-"], capture_output=True, text=True).stdout
        buenos = [os.path.join(sal, n) for n in hechos if n.endswith(".MP4")]
        assert len(buenos) == 2 and len(rotos) == 1, (buenos, rotos)
        refs = {md5(orig1), md5(orig2)}
        for b in buenos:
            assert md5(b) in refs, f"{b} no coincide con el original"
        assert any("2026-10-01" in b for b in buenos), "falta la fecha en el nombre"
    print("OK: 2 videos reparados idénticos al original y 1 detectado como incompleto")


if __name__ == "__main__":
    main()
