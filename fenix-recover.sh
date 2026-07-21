#!/usr/bin/env bash
set -euo pipefail

APP="fenix-recover"
VERSION="1.0.0"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1" >&2; }

# Defaults
TYPES="all"
QUIET=0
DEDUP=1
DDRESCUE=0
FOREMOST_FLAGS="-v -T"
PHOTOREC_FLAGS=""
DD_FLAGS="-d -r3"
INPUT=""
OUTDIR=""
MODE_FOREMOST=1
MODE_PHOTOREC=1
MODE_MERGE=1

# Load config
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/fenix-recover"
CONFIG_FILE="$CONFIG_DIR/fenix-recover.conf"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/fenix-recover.conf" ]]; then
    source "$SCRIPT_DIR/fenix-recover.conf"
elif [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
fi

usage() {
    cat <<EOF
$APP v$VERSION - Super script de recuperación (Foremost + PhotoRec fusionados)

USO:
  sudo ./fenix-recover.sh -i <input> -o <output> [opciones]

REQUERIDO:
  -i <dispositivo|imagen>   Fuente a recuperar (/dev/sdb, imagen.dd)

OPCIONAL:
  -o <directorio>           Directorio base de salida (default: ./recovery_<ts>)
  -t <tipos>                Tipos específicos (jpg,pdf,zip... default: todos)
  --foremost-only           Solo ejecutar foremost
  --photorec-only           Solo ejecutar photorec
  --merge-only              Solo dedup (asume foremost/ y photorec/ ya existen)
  --ddrescue                Crear imagen con ddrescue antes de tallar
  --no-dedup                No deduplicar (dejar outputs raw)
  -q                        Modo silencioso
  -h                        Ayuda

EJEMPLOS:
  sudo ./fenix-recover.sh -i /dev/sdb -o ~/recuperado
  sudo ./fenix-recover.sh -i /dev/sdb -t jpg,pdf --ddrescue
  ./fenix-recover.sh -i imagen.dd -o ~/recuperado
  ./fenix-recover.sh --merge-only -o ~/recuperado
EOF
    exit 0
}

cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        warn "Script interrumpido. Los outputs parciales están en: ${OUTDIR:-N/A}"
    fi
    exit $exit_code
}
trap cleanup EXIT INT TERM

check_deps() {
    local missing=()
    for cmd in foremost photorec python3; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        warn "Faltan herramientas: ${missing[*]}"
        if [[ -f /etc/debian_version ]]; then
            log "Instalando: ${missing[*]}..."
            apt-get update -qq && apt-get install -y -qq foremost testdisk python3
        else
            err "Instala manualmente: ${missing[*]}"
            err "  Debian/Ubuntu: sudo apt-get install foremost testdisk python3"
            err "  Arch:          sudo pacman -S foremost testdisk python"
            exit 1
        fi
    fi
    log "Dependencias OK"
}

validate_input() {
    local input="$1"
    if [[ -b "$input" ]]; then
        log "Input: dispositivo de bloques $input"
        return 0
    elif [[ -f "$input" ]]; then
        log "Input: archivo de imagen $input"
        return 0
    else
        err "Input no válido: $input (debe ser dispositivo de bloques o archivo)"
        exit 1
    fi
}

list_devices() {
    log "Dispositivos disponibles:"
    lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL | grep -E "disk|part" || true
    echo ""
    read -r -p "Dispositivo (ej: /dev/sdb): " sel_dev
    if [[ -z "$sel_dev" || ! -b "$sel_dev" ]]; then
        err "Dispositivo inválido"
        exit 1
    fi
    INPUT="$sel_dev"
}

run_ddrescue() {
    local src="$1" img="$2" logf="$3"
    if command -v ddrescue &>/dev/null; then
        log "Creando imagen con ddrescue (esto puede llevar mucho tiempo)..."
        warn "Espacio necesario: al menos el tamaño del dispositivo"
        read -r -p "Confirmar creación de imagen de $src? (s/N): " confirm
        if [[ "$confirm" =~ ^[sSyY] ]]; then
            log "ddrescue $DD_FLAGS $src $img $logf"
            ddrescue $DD_FLAGS "$src" "$img" "$logf" || warn "ddrescue reportó errores (log: $logf)"
            log "Imagen creada: $img"
            return 0
        else
            warn "ddrescue cancelado, trabajando sobre dispositivo directamente"
            return 1
        fi
    else
        warn "ddrescue no instalado. Instalar: apt-get install ddrescue"
        return 1
    fi
}

run_foremost() {
    local input="$1" outdir="$2" types="$3"
    log "=== FASE 1: Foremost ==="
    mkdir -p "$outdir/foremost"
    local cmd="foremost $FOREMOST_FLAGS"
    if [[ "$types" != "all" ]]; then
        cmd="$cmd -t $types"
    fi
    cmd="$cmd -i $input -o $outdir/foremost"
    log "Ejecutando: $cmd"
    SECONDS=0
    if [[ $QUIET -eq 1 ]]; then
        $cmd &>/dev/null || warn "Foremost terminó con código $?"
    else
        $cmd || warn "Foremost terminó con código $?"
    fi
    local elapsed=$SECONDS
    local count=0
    if [[ -f "$outdir/foremost/audit.txt" ]]; then
        count=$(grep -cP '^\d+:' "$outdir/foremost/audit.txt" 2>/dev/null || echo 0)
    fi
    log "Foremost: $count archivos recuperados en ${elapsed}s"
    echo "$count:$elapsed" > "$outdir/.foremost_stats"
}

run_photorec() {
    local input="$1" outdir="$2" types="$3"
    log "=== FASE 2: PhotoRec ==="
    mkdir -p "$outdir/photorec"
    local photorec_out="$outdir/photorec"
    local cmd="photorec /d $photorec_out"
    if [[ $QUIET -eq 0 ]]; then
        cmd="$cmd /log"
    fi
    if [[ "$types" != "all" ]]; then
        local fileopt="fileopt,everything,disable"
        IFS=',' read -ra tarr <<< "$types"
        for t in "${tarr[@]}"; do
            fileopt="$fileopt,${t},enable"
        done
        cmd="$cmd /cmd $input $fileopt,search"
    else
        cmd="$cmd /cmd $input fileopt,everything,enable,search"
    fi
    log "Ejecutando: photorec /d $photorec_out ..."
    SECONDS=0
    if [[ $QUIET -eq 1 ]]; then
        $cmd &>/dev/null || warn "PhotoRec terminó con código $?"
    else
        $cmd || warn "PhotoRec terminó con código $?"
    fi
    local elapsed=$SECONDS
    local count=0
    local recup_dirs=()
    while IFS= read -r d; do
        recup_dirs+=("$d")
    done < <(find "$photorec_out" -maxdepth 1 -type d -name 'recup_dir.*' 2>/dev/null | sort)
    if [[ ${#recup_dirs[@]} -gt 0 ]]; then
        for d in "${recup_dirs[@]}"; do
            local c
            c=$(find "$d" -type f 2>/dev/null | wc -l)
            count=$((count + c))
        done
    fi
    if [[ $count -eq 0 ]]; then
        count=$(find "$photorec_out" -type f 2>/dev/null | wc -l)
    fi
    log "PhotoRec: $count archivos recuperados en ${elapsed}s"
    echo "$count:$elapsed" > "$outdir/.photorec_stats"
}

run_dedup() {
    local outdir="$1"
    log "=== FASE 3: Deduplicación y Merge ==="
    local py="$SCRIPT_DIR/fenix-dedup.py"
    if [[ ! -f "$py" ]]; then
        py=$(command -v fenix-dedup.py 2>/dev/null || true)
    fi
    if [[ -z "$py" || ! -f "$py" ]]; then
        err "fenix-dedup.py no encontrado en $SCRIPT_DIR ni en PATH"
        err "El merge manual está en: $outdir/foremost/ y $outdir/photorec/"
        return 1
    fi
    log "Ejecutando: python3 $py ..."
    python3 "$py" \
        --foremost "$outdir/foremost" \
        --photorec "$outdir/photorec" \
        --merged "$outdir/merged" \
        --db "$outdir/hashes.db" \
        --report "$outdir/report.json"
    log "Deduplicación completada"
}

show_stats() {
    local outdir="$1"
    echo ""
    echo "=============================================="
    echo -e "  ${CYAN}FENIX-RECOVER - RESUMEN${NC}"
    echo "=============================================="
    if [[ -f "$outdir/.foremost_stats" ]]; then
        local f_count f_elapsed
        IFS=':' read -r f_count f_elapsed < "$outdir/.foremost_stats"
        echo -e "  ${GREEN}Foremost:${NC}  $f_count archivos en ${f_elapsed}s"
    fi
    if [[ -f "$outdir/.photorec_stats" ]]; then
        local p_count p_elapsed
        IFS=':' read -r p_count p_elapsed < "$outdir/.photorec_stats"
        echo -e "  ${GREEN}PhotoRec:${NC}  $p_count archivos en ${p_elapsed}s"
    fi
    if [[ -f "$outdir/report.json" ]]; then
        local unique dups
        unique=$(python3 -c "import json; d=json.load(open('$outdir/report.json')); print(d.get('dedup',{}).get('unique','?'))" 2>/dev/null || echo "?")
        dups=$(python3 -c "import json; d=json.load(open('$outdir/report.json')); print(d.get('dedup',{}).get('duplicates_removed','?'))" 2>/dev/null || echo "?")
        echo -e "  ${GREEN}Únicos:${NC}     $unique archivos"
        echo -e "  ${YELLOW}Duplicados:${NC}  $dups eliminados"
    fi
    if [[ -d "$outdir/merged" ]]; then
        local total
        total=$(find "$outdir/merged" -type f 2>/dev/null | wc -l)
        echo -e "  ${CYAN}Total merged:${NC} $total archivos"
    fi
    echo "  ${CYAN}Salida:${NC}     $outdir"
    echo "=============================================="
    echo ""
    warn "Revisa los archivos antes de borrar el output de las herramientas raw"
    warn "Para comprimir: tar czf ${outdir}.tar.gz -C \"$(dirname "$outdir")\" \"$(basename "$outdir")\""
}

# ---- MAIN ----

# Auto-sudo if not root (skip for help)
for arg in "$@"; do
    if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
        usage
    fi
done
if [[ $EUID -ne 0 ]]; then
    echo "[!] $APP necesita privilegios root. Re-ejecutando con sudo..."
    exec sudo "$0" "$@"
fi

# Interactive mode
if [[ $# -eq 0 ]]; then
    echo "$APP v$VERSION - Super script de recuperación Foremost + PhotoRec"
    echo ""
    list_devices
    read -r -p "Directorio de salida (default: ./recovery_$(date +%Y%m%d_%H%M%S)): " out_sel
    OUTDIR="${out_sel:-./recovery_$(date +%Y%m%d_%H%M%S)}"
    read -r -p "Tipos de archivo (default: todos, ej: jpg,pdf): " types_sel
    TYPES="${types_sel:-all}"
    read -r -p "Crear imagen con ddrescue primero? (s/N): " dd_sel
    if [[ "$dd_sel" =~ ^[sSyY] ]]; then
        DDRESCUE=1
    fi
else
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -i) INPUT="$2"; shift 2 ;;
            -o) OUTDIR="$2"; shift 2 ;;
            -t) TYPES="$2"; shift 2 ;;
            --foremost-only) MODE_PHOTOREC=0; MODE_MERGE=0; shift ;;
            --photorec-only) MODE_FOREMOST=0; MODE_MERGE=0; shift ;;
            --merge-only) MODE_FOREMOST=0; MODE_PHOTOREC=0; shift ;;
            --ddrescue) DDRESCUE=1; shift ;;
            --no-dedup) DEDUP=0; shift ;;
            -q) QUIET=1; shift ;;
            -h) usage ;;
            *) err "Opción desconocida: $1"; usage ;;
        esac
    done
    if [[ $MODE_MERGE -eq 0 && $MODE_FOREMOST -eq 0 && $MODE_PHOTOREC -eq 0 ]]; then
        : # merge-only, no necesita input
    elif [[ -z "$INPUT" ]]; then
        err "Flag -i es requerido (o usa --merge-only)"
        usage
    fi
fi

if [[ -z "$OUTDIR" ]]; then
    OUTDIR="./recovery_$(date +%Y%m%d_%H%M%S)"
fi
OUTDIR="$(realpath -m "$OUTDIR")"

echo ""
log "$APP v$VERSION"
log "Input:  ${INPUT:-N/A (merge-only)}"
log "Output: $OUTDIR"
log "Tipos:  $TYPES"
echo ""

# Checks
check_deps
if [[ -n "$INPUT" ]]; then
    validate_input "$INPUT"
fi
mkdir -p "$OUTDIR"

# ddrescue
if [[ $DDRESCUE -eq 1 && -n "$INPUT" && -b "$INPUT" ]]; then
    local img="$OUTDIR/disk_image.dd"
    local logf="$OUTDIR/ddrescue.log"
    if run_ddrescue "$INPUT" "$img" "$logf"; then
        INPUT="$img"
    fi
fi

# Foremost
if [[ $MODE_FOREMOST -eq 1 && -n "$INPUT" ]]; then
    run_foremost "$INPUT" "$OUTDIR" "$TYPES"
fi

# PhotoRec
if [[ $MODE_PHOTOREC -eq 1 && -n "$INPUT" ]]; then
    run_photorec "$INPUT" "$OUTDIR" "$TYPES"
fi

# Dedup
if [[ $DEDUP -eq 1 ]]; then
    if [[ -d "$OUTDIR/foremost" && -d "$OUTDIR/photorec" ]] || [[ $MODE_MERGE -eq 1 ]]; then
        run_dedup "$OUTDIR"
    else
        warn "Saltando dedup: faltan foremost/ o photorec/"
    fi
fi

# Stats
show_stats "$OUTDIR"
