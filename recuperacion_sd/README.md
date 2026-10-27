# Recuperar fotos y videos de una tarjeta SD Sony (en Mac)

Recupera fotos y videos de cámaras **Sony** (Alpha, ZV, RX, Handycam) cuando:

- se **borraron** desde la cámara o desde el Mac, o
- la tarjeta se **formateó desde la cámara** (Menú → Formatear).

| Sony graba | Se recupera como |
|------------|------------------|
| Fotos JPEG | `.jpg` |
| Fotos RAW | `.arw` |
| Fotos HEIF (A7 IV, A1, ZV-E1…) | `.heic` (es el mismo formato que `.HIF`) |
| Video XAVC S / XAVC HS / XAVC S-I | `.mp4` |
| Video AVCHD | `.mts` |

## ⚠️ Antes de empezar

1. **No vuelvas a usar la tarjeta** en la cámara: cada foto nueva puede tapar
   las anteriores.
2. **Pon el seguro** de la tarjeta (la pestaña del costado) en **LOCK** antes de
   meterla al Mac. Así macOS no puede escribir nada en ella.
3. Si el Mac dice *"El disco no es legible: Inicializar / Ignorar / Expulsar"*,
   elige **Ignorar**.
4. Necesitas espacio libre de unas **2 veces el tamaño de la tarjeta**
   (ej. 128 GB libres para una de 64 GB). Si no tienes, usa un disco externo.

## Paso a paso (Mac)

1. Instala Python 3 desde https://www.python.org/downloads/macos/
2. Descarga esta carpeta (`recuperar_sd.py` y `recuperar_mac.command`), por
   ejemplo en *Descargas*.
3. Mete la tarjeta en el Mac (con el seguro en LOCK).
4. Abre **Terminal** (Cmd + Espacio → escribe *Terminal*) y ejecuta:

```bash
cd ~/Downloads/recuperacion_sd
bash recuperar_mac.command
```

El asistente:
1. te muestra los discos externos y te pide el número de la tarjeta;
2. hace una copia completa de la tarjeta (solo lectura; pide tu contraseña del Mac);
3. busca fotos y videos en esa copia;
4. abre la carpeta `Recuperacion_SD_...` del Escritorio con los resultados.

Para guardar en un disco externo:

```bash
bash recuperar_mac.command /Volumes/NombreDelDisco
```

Si aparece **"Operation not permitted"**: *Ajustes del Sistema → Privacidad y
seguridad → Acceso total al disco* → activa **Terminal** y repite.

## Uso manual (opcional)

```bash
diskutil list external                   # ej. la tarjeta es /dev/disk4
diskutil unmountDisk /dev/disk4
sudo python3 recuperar_sd.py imagen /dev/rdisk4 tarjeta.img
python3 recuperar_sd.py recuperar tarjeta.img --salida recuperados

# Solo videos / solo RAW / todos los formatos que conoce el programa
python3 recuperar_sd.py recuperar tarjeta.img --salida videos --tipos mp4,mts
python3 recuperar_sd.py recuperar tarjeta.img --salida raw --tipos arw
python3 recuperar_sd.py recuperar tarjeta.img --salida todo --tipos todos
```

## ¿Por qué funciona después de formatear?

El "Formatear" normal de la cámara solo crea un índice nuevo y vacío: las
fotos y videos siguen físicamente en la tarjeta. El programa no usa ese índice:
lee la tarjeta entera buscando cómo empieza cada archivo (por ejemplo, un
ARW de Sony empieza con `II*` y dice `SONY` dentro) y calcula dónde termina
leyendo su estructura interna.

## Cuándo NO se puede recuperar

- Si **grabaste fotos o videos nuevos** después de formatear: lo nuevo tapa lo
  viejo (se pierde sobre todo lo que estaba al principio de la tarjeta).
- Si usaste **Formato completo** (*Full Format*, en modelos como A7 IV, A7S III,
  A1, ZV-E1): borra toda la tarjeta con ceros.
- Videos grabados con **grabación proxy** activada pueden salir dañados,
  porque la cámara graba dos archivos a la vez y quedan intercalados.
- La tarjeta no aparece en el Mac o da errores de lectura constantes: es daño
  físico y necesita un laboratorio.

Los nombres originales (`DSC01234.ARW`, `C0001.MP4`) no se recuperan; los
archivos salen numerados y agrupados en carpetas por tipo.

Si falta algo, prueba también **PhotoRec** (gratuito, https://www.cgsecurity.org)
con la misma copia `tarjeta.img`.

## Pruebas automáticas

```bash
pip install pillow   # además: ffmpeg, dosfstools y mtools para prueba_sony.py
python3 pruebas/prueba_sony.py          # tarjeta Sony simulada y formateada
python3 pruebas/prueba_recuperacion.py  # otros formatos
```
