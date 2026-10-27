# Recuperar archivos borrados de una memoria SD

Programa en Python (sin instalar nada extra) que busca archivos borrados en una
tarjeta SD o memoria USB y los guarda en una carpeta.

## ⚠️ Antes de empezar (muy importante)

1. **No uses la tarjeta**: no tomes fotos, no copies archivos, no la formatees.
   Todo lo que escribas puede sobrescribir lo que quieres recuperar.
2. Si Windows dice *"Hay que formatear el disco"*, responde **Cancelar**.
3. Guarda la imagen y los archivos recuperados **en tu computadora**, nunca en la SD.
   Necesitas espacio libre de al menos el tamaño de la tarjeta (ej. 32 GB) más lo recuperado.

## Qué recupera

| Tipo | Formatos |
|------|----------|
| Fotos | jpg, png, gif, webp, heic, avif |
| Video | mp4, mov, 3gp, avi |
| Audio | mp3, wav, m4a |
| Documentos | pdf, docx, xlsx, pptx, zip, apk |

Los nombres originales y carpetas **no** se recuperan: los archivos salen como
`jpg_00001_off....jpg`, agrupados por tipo.

## Paso a paso en Windows

1. Instala Python desde https://www.python.org/downloads/ (marca *"Add Python to PATH"*).
2. Descarga `recuperar_sd.py` a una carpeta, por ejemplo `C:\Recuperar`.
3. Conecta la SD y mira qué letra tiene (ej. `E:`).
4. Abre **Símbolo del sistema como Administrador** (clic derecho → *Ejecutar como administrador*).
5. Ejecuta:

```bat
cd C:\Recuperar
python recuperar_sd.py imagen \\.\E: tarjeta.img
python recuperar_sd.py recuperar tarjeta.img --salida recuperados
```

6. Abre la carpeta `C:\Recuperar\recuperados`.

## Linux / macOS

```bash
lsblk                     # Linux: identifica la SD, ej. /dev/sdb
diskutil list             # macOS: ej. /dev/disk4  → usa /dev/rdisk4
sudo python3 recuperar_sd.py imagen /dev/sdb tarjeta.img
python3 recuperar_sd.py recuperar tarjeta.img --salida recuperados
```

## Opciones útiles

```bash
# Solo fotos
python recuperar_sd.py recuperar tarjeta.img --salida fotos --tipos jpg,png,heic
# Solo videos
python recuperar_sd.py recuperar tarjeta.img --salida videos --tipos mp4,mov
```

## Cómo funciona

1. **`imagen`** copia la tarjeta completa, byte a byte, a un archivo `.img`. Solo
   lee la SD. Si hay sectores dañados los rellena con ceros y sigue.
2. **`recuperar`** recorre la imagen buscando la "firma" con la que empieza cada
   tipo de archivo (ej. una foto JPEG empieza con `FF D8 FF`), lee su estructura
   interna para saber dónde termina y lo guarda.

## Límites

- Si los archivos se **sobrescribieron** (se grabaron cosas nuevas encima), no se pueden recuperar.
- Archivos **fragmentados** (guardados en trozos separados) pueden salir
  incompletos o dañados. En cámaras y celulares es poco común.
- Si la tarjeta **no aparece** en la computadora o hace ruido/se calienta, es
  daño físico: necesita un laboratorio de recuperación.
- Algunos celulares Android **cifran** la SD; en ese caso los datos no se pueden leer fuera del teléfono.

Si este programa no encuentra lo que buscas, prueba también **PhotoRec**
(gratuito, https://www.cgsecurity.org) usando la misma imagen `tarjeta.img`.

## Prueba automática

```bash
pip install pillow
python pruebas/prueba_recuperacion.py
```
