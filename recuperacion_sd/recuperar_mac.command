#!/bin/bash
# Asistente para Mac: recupera fotos y videos de una tarjeta SD de cámara Sony,
# incluso si se borraron o si la tarjeta se formateó desde la cámara.
#
# Uso (en Terminal):
#   bash recuperar_mac.command                    # guarda todo en el Escritorio
#   bash recuperar_mac.command /Volumes/MiDisco   # guarda en un disco externo

set -u
cd "$(dirname "$0")" || exit 1
PROGRAMA="$PWD/recuperar_sd.py"
DESTINO_BASE="${1:-$HOME/Desktop}"

linea() { printf '%s\n' "------------------------------------------------------------"; }
salir() { echo; echo "❌ $1"; echo; exit 1; }

clear
linea
echo "  RECUPERACIÓN DE FOTOS Y VIDEOS - TARJETA SD SONY"
linea
echo
echo "Antes de seguir:"
echo "  • No tomes fotos ni grabes con esa tarjeta."
echo "  • Si el Mac pregunta si quieres inicializar/formatear el disco: IGNORAR."
echo "  • Consejo: pon el seguro de la tarjeta (la pestaña del costado) en LOCK"
echo "    antes de meterla; así el Mac no puede escribir nada en ella."
echo

[ -f "$PROGRAMA" ] || salir "No encuentro recuperar_sd.py. Debe estar en la misma carpeta que este archivo."
command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' 2>/dev/null ||
    salir "Falta Python 3. Instálalo desde https://www.python.org/downloads/macos/ y vuelve a ejecutar."
[ -d "$DESTINO_BASE" ] || salir "La carpeta destino no existe: $DESTINO_BASE"

echo "Discos externos conectados:"
echo
diskutil list external physical
echo
read -r -p "Escribe el NÚMERO del disco de tu tarjeta (ej. 4 si dice /dev/disk4): " N
[[ "$N" =~ ^[0-9]+$ ]] || salir "Número no válido."
[ "$N" -ne 0 ] || salir "disk0 es el disco interno del Mac. Elige el de la tarjeta."

INFO="$(diskutil info "/dev/disk$N" 2>/dev/null)" || salir "No existe /dev/disk$N."
if echo "$INFO" | grep -qE "Virtual: +Yes|APPLE SSD|Apple Fabric"; then
    salir "/dev/disk$N parece ser un disco del propio Mac, no la tarjeta."
fi
NOMBRE="$(echo "$INFO" | sed -n 's/^ *Device \/ Media Name: *//p')"
TAMANO="$(echo "$INFO" | sed -n 's/^ *Disk Size:.*(\([0-9]*\) Bytes).*/\1/p' | head -1)"
[ -n "$TAMANO" ] || salir "No pude leer el tamaño de /dev/disk$N."

echo
echo "Disco elegido: /dev/disk$N  —  $NOMBRE  —  $((TAMANO / 1000000000)) GB"
read -r -p "¿Es tu tarjeta SD? (s/n): " OK
[[ "$OK" =~ ^[sS] ]] || salir "Cancelado."

# Se necesita espacio para la copia de la tarjeta + lo recuperado (~2 veces su tamaño)
LIBRE=$(( $(df -k "$DESTINO_BASE" | awk 'NR==2 {print $4}') * 1024 ))
if [ "$LIBRE" -lt $((TAMANO * 2)) ]; then
    echo
    echo "⚠️  Espacio libre en destino: $((LIBRE / 1000000000)) GB."
    echo "   Se recomiendan $((TAMANO * 2 / 1000000000)) GB (copia de la tarjeta + archivos recuperados)."
    echo "   Puedes usar un disco externo:  bash recuperar_mac.command /Volumes/NombreDelDisco"
    [ "$LIBRE" -gt "$TAMANO" ] || salir "No hay espacio ni para la copia de la tarjeta."
    read -r -p "¿Continuar de todas formas? (s/n): " OK
    [[ "$OK" =~ ^[sS] ]] || salir "Cancelado."
fi

CARPETA="$DESTINO_BASE/Recuperacion_SD_$(date +%Y%m%d_%H%M)"
mkdir -p "$CARPETA" || salir "No pude crear $CARPETA"
IMAGEN="$CARPETA/tarjeta.img"

echo
linea
echo "PASO 1/2: copiando la tarjeta (solo lectura). Puede tardar bastante:"
echo "          ~10-20 min por cada 32 GB. Pedirá la contraseña de tu Mac."
linea
diskutil unmountDisk "/dev/disk$N" >/dev/null 2>&1
if ! sudo python3 "$PROGRAMA" imagen "/dev/rdisk$N" "$IMAGEN"; then
    echo
    echo "Si dice 'Operation not permitted': abre Ajustes del Sistema > Privacidad y"
    echo "seguridad > Acceso total al disco, activa 'Terminal' y vuelve a intentarlo."
    salir "No se pudo copiar la tarjeta."
fi
sudo chown "$(id -un)" "$IMAGEN"
echo
echo "✅ Copia lista. Ya puedes expulsar la tarjeta y guardarla sin usarla."

echo
linea
echo "PASO 2/2: buscando fotos (JPG, ARW, HIF) y videos (MP4, MTS)..."
linea
python3 "$PROGRAMA" recuperar "$IMAGEN" --salida "$CARPETA/recuperados" || salir "Falló la búsqueda."

echo
linea
echo "✅ LISTO. Los archivos están en:"
echo "   $CARPETA/recuperados"
echo
echo "Cuando hayas revisado que todo está bien, puedes borrar"
echo "tarjeta.img para liberar espacio."
linea
open "$CARPETA/recuperados" 2>/dev/null
