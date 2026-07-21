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
VALIDATE=1
DDRESCUE=0
FOREMOST_FLAGS="-v -T"
PHOTOREC_FLAGS=""
DD_FLAGS="-d -r3"
INPUT=""
OUTDIR=""
MODE_FOREMOST=1
MODE_PHOTOREC=1

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
  --foremost-only           Solo ejecutar Foremost
  --photorec-only           Solo ejecutar PhotoRec
  --ddrescue                Crear imagen con ddrescue antes de tallar
  --no-validate             Skip validación de archivos
  --validate-only           Solo validar salida existente
  -q                        Modo silencioso
  -h                        Ayuda

EJEMPLOS:
  sudo ./fenix-recover.sh -i /dev/sdb -o ~/recuperado
  sudo ./fenix-recover.sh -i /dev/sdb -t jpg,pdf --ddrescue
  ./fenix-recover.sh -i imagen.dd -o ~/recuperado
  ./fenix-recover.sh --validate-only -o ~/recuperado
EOF
    exit 0
}

cleanup() {
    local ec=$?
    if [[ $ec -ne 0 ]]; then
        warn "Script interrumpido. Output parcial en: ${OUTDIR:-N/A}"
    fi
    exit $ec
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
            err "Instala manualmente:"
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
        err "Input no válido: $input (debe ser dispositivo o archivo)"
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
        log "Creando imagen con ddrescue..."
        warn "Espacio necesario: al menos el tamaño del dispositivo"
        read -r -p "Confirmar creación de imagen de $src? (s/N): " confirm
        if [[ "$confirm" =~ ^[sSyY] ]]; then
            log "ddrescue $DD_FLAGS $src $img $logf"
            ddrescue $DD_FLAGS "$src" "$img" "$logf" || warn "ddrescue reportó errores"
            log "Imagen creada: $img"
            return 0
        else
            warn "ddrescue cancelado, trabajando sobre dispositivo"
            return 1
        fi
    else
        warn "ddrescue no instalado (apt-get install ddrescue)"
        return 1
    fi
}

run_foremost() {
    local input="$1" outdir="$2" types="$3"
    log "=== FASE 1: Foremost ==="
    local tmp="$outdir/.tmp/foremost"
    mkdir -p "$tmp"
    local cmd="foremost $FOREMOST_FLAGS"
    [[ "$types" != "all" ]] && cmd="$cmd -t $types"
    cmd="$cmd -i $input -o $tmp"
    log "Ejecutando: foremost ..."
    SECONDS=0
    if [[ $QUIET -eq 1 ]]; then
        $cmd &>/dev/null || warn "Foremost terminó con código $?"
    else
        $cmd || warn "Foremost terminó con código $?"
    fi
    local elapsed=$SECONDS
    local count=0
    if [[ -f "$tmp/audit.txt" ]]; then
        count=$(grep -cP '^\d+:' "$tmp/audit.txt" 2>/dev/null || echo 0)
    fi
    log "Foremost: $count archivos en ${elapsed}s"
    echo "$count:$elapsed" > "$outdir/.foremost_stats"
}

run_photorec() {
    local input="$1" outdir="$2" types="$3"
    log "=== FASE 2: PhotoRec ==="
    local tmp="$outdir/.tmp/photorec"
    mkdir -p "$tmp"
    local cmd="photorec /d $tmp"
    [[ $QUIET -eq 0 ]] && cmd="$cmd /log"
    if [[ "$types" != "all" ]]; then
        local fileopt="fileopt,everything,disable"
        IFS=',' read -ra tarr <<< "$types"
        for t in "${tarr[@]}"; do fileopt="$fileopt,${t},enable"; done
        cmd="$cmd /cmd $input $fileopt,search"
    else
        cmd="$cmd /cmd $input fileopt,everything,enable,search"
    fi
    log "Ejecutando: photorec ..."
    SECONDS=0
    if [[ $QUIET -eq 1 ]]; then
        $cmd &>/dev/null || warn "PhotoRec terminó con código $?"
    else
        $cmd || warn "PhotoRec terminó con código $?"
    fi
    local elapsed=$SECONDS
    local count=0
    while IFS= read -r d; do
        local c; c=$(find "$d" -type f 2>/dev/null | wc -l)
        count=$((count + c))
    done < <(find "$tmp" -maxdepth 1 -type d -name 'recup_dir.*' 2>/dev/null | sort)
    [[ $count -eq 0 ]] && count=$(find "$tmp" -type f 2>/dev/null | wc -l)
    log "PhotoRec: $count archivos en ${elapsed}s"
    echo "$count:$elapsed" > "$outdir/.photorec_stats"
}

run_dedup() {
    local outdir="$1"
    log "=== FASE 3: Deduplicación y copia a salida única ==="
    local py="$SCRIPT_DIR/fenix-dedup.py"
    [[ ! -f "$py" ]] && py=$(command -v fenix-dedup.py 2>/dev/null || true)
    if [[ -z "$py" || ! -f "$py" ]]; then
        err "fenix-dedup.py no encontrado"
        return 1
    fi
    local ftmp="$outdir/.tmp/foremost"
    local ptmp="$outdir/.tmp/photorec"
    if [[ ! -d "$ftmp" && ! -d "$ptmp" ]]; then
        warn "No hay .tmp/foremost/ ni .tmp/photorec/ - saltando dedup"
        return 1
    fi
    log "Ejecutando: python3 $py ..."
    python3 "$py" \
        --foremost-tmp "$ftmp" \
        --photorec-tmp "$ptmp" \
        --output "$outdir" \
        --db "$outdir/hashes.db" \
        --report "$outdir/report.json"
    log "Deduplicación completada"
    # Cleanup .tmp
    if [[ -d "$outdir/.tmp" ]]; then
        rm -rf "$outdir/.tmp"
        log "Directorio temporal .tmp/ eliminado"
    fi
}

run_validate() {
    local outdir="$1"
    log "=== FASE 4: Validación y reparación ==="
    local py="$SCRIPT_DIR/fenix-validate.py"
    [[ ! -f "$py" ]] && py=$(command -v fenix-validate.py 2>/dev/null || true)
    if [[ -z "$py" || ! -f "$py" ]]; then
        err "fenix-validate.py no encontrado"
        return 1
    fi
    if [[ ! -d "$outdir" ]]; then
        warn "No existe $outdir - saltando validación"
        return 1
    fi
    # Check if there are any files to validate (skip metadata dirs)
    local has_files
    has_files=$(find "$outdir" -maxdepth 2 -type f 2>/dev/null | head -5 | wc -l)
    if [[ "$has_files" -eq 0 ]]; then
        warn "No hay archivos para validar en $outdir"
        return 1
    fi
    log "Ejecutando: python3 $py ..."
    python3 "$py" \
        --input "$outdir" \
        --valid "$outdir/valid" \
        --repair "$outdir/repaired" \
        --corrupt "$outdir/corrupt" \
        --report "$outdir/validate_report.json"
    local vcount rcount ccount
    vcount=$(python3 -c "import json; d=json.load(open('$outdir/validate_report.json')); print(d.get('valid',0))" 2>/dev/null || echo "?")
    rcount=$(python3 -c "import json; d=json.load(open('$outdir/validate_report.json')); print(d.get('repaired',0))" 2>/dev/null || echo "?")
    ccount=$(python3 -c "import json; d=json.load(open('$outdir/validate_report.json')); print(d.get('corrupt',0))" 2>/dev/null || echo "?")
    log "Validación: $vcount válidos, $rcount reparados, $ccount corruptos"
    echo "$vcount:$rcount:$ccount" > "$outdir/.validate_stats"
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
        local total_out
        total_out=$(python3 -c "import json; d=json.load(open('$outdir/report.json')); print(d.get('total_files','?'))" 2>/dev/null || echo "?")
        local fore
        fore=$(python3 -c "import json; d=json.load(open('$outdir/report.json')); print(d['foremost'].get('copied','?'))" 2>/dev/null || echo "?")
        local photo
        photo=$(python3 -c "import json; d=json.load(open('$outdir/report.json')); print(d['photorec'].get('copied','?'))" 2>/dev/null || echo "?")
        local dups
        dups=$(python3 -c "import json; d=json.load(open('$outdir/report.json')); print(d['photorec'].get('duplicates_skipped','?'))" 2>/dev/null || echo "?")
        echo -e "  ${CYAN}Salida única:${NC} $total_out archivos totales"
        echo -e "  ${GREEN}  F_ (Foremost):${NC} $fore"
        echo -e "  ${GREEN}  P_ (PhotoRec):${NC} $photo"
        echo -e "  ${YELLOW}  Duplicados evitados:${NC} $dups"
    fi
    if [[ -f "$outdir/.validate_stats" ]]; then
        local v r c
        IFS=':' read -r v r c < "$outdir/.validate_stats"
        echo -e "  ${GREEN}Válidos:${NC}    $v | ${YELLOW}Reparados:${NC} $r | ${RED}Corruptos:${NC} $c"
    fi
    echo "  ${CYAN}Salida:${NC}     $outdir"
    echo "=============================================="
    echo ""
    warn "Revisa los archivos antes de borrar datos fuente"
    warn "Para comprimir: tar czf ${outdir}.tar.gz -C \"$(dirname "$outdir")\" \"$(basename "$outdir")\""
}

# ---- MAIN ----

# Help check before sudo
for arg in "$@"; do [[ "$arg" == "-h" || "$arg" == "--help" ]] && usage; done

# Auto-sudo
if [[ $EUID -ne 0 ]]; then
    echo "[!] $APP necesita privilegios root. Re-ejecutando con sudo..."
    exec sudo "$0" "$@"
fi

# Interactivo o args
if [[ $# -eq 0 ]]; then
    echo "$APP v$VERSION - Super script de recuperación Foremost + PhotoRec"
    echo ""
    list_devices
    read -r -p "Directorio de salida (default: ./recovery_$(date +%Y%m%d_%H%M%S)): " out_sel
    OUTDIR="${out_sel:-./recovery_$(date +%Y%m%d_%H%M%S)}"
    read -r -p "Tipos de archivo (default: todos, ej: jpg,pdf): " types_sel
    TYPES="${types_sel:-all}"
    read -r -p "Crear imagen con ddrescue primero? (s/N): " dd_sel
    [[ "$dd_sel" =~ ^[sSyY] ]] && DDRESCUE=1
    read -r -p "Validar archivos recuperados? (S/n): " val_sel
    [[ "$val_sel" =~ ^[nN] ]] && VALIDATE=0
else
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -i) INPUT="$2"; shift 2 ;;
            -o) OUTDIR="$2"; shift 2 ;;
            -t) TYPES="$2"; shift 2 ;;
            --foremost-only) MODE_PHOTOREC=0; shift ;;
            --photorec-only) MODE_FOREMOST=0; shift ;;
            --ddrescue) DDRESCUE=1; shift ;;
            --no-validate) VALIDATE=0; shift ;;
            --validate-only) MODE_FOREMOST=0; MODE_PHOTOREC=0; VALIDATE=1; INPUT=""; shift ;;
            -q) QUIET=1; shift ;;
            -h) usage ;;
            *) err "Opción desconocida: $1"; usage ;;
        esac
    done
    if [[ $MODE_FOREMOST -eq 1 || $MODE_PHOTOREC -eq 1 ]]; then
        [[ -z "$INPUT" ]] && { err "Flag -i requerido"; usage; }
    fi
fi

# Output dir
if [[ -z "$OUTDIR" ]]; then
    OUTDIR="./recovery_$(date +%Y%m%d_%H%M%S)"
fi
OUTDIR="$(realpath -m "$OUTDIR")"

echo ""
log "$APP v$VERSION"
log "Input:  ${INPUT:-N/A (validate-only)}"
log "Output: $OUTDIR"
log "Tipos:  $TYPES"
echo ""

check_deps
[[ -n "$INPUT" ]] && validate_input "$INPUT"
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

# Dedup (copia de .tmp/ a salida única)
if [[ -d "$OUTDIR/.tmp" ]]; then
    run_dedup "$OUTDIR"
else
    [[ $MODE_FOREMOST -eq 1 || $MODE_PHOTOREC -eq 1 ]] && warn "No hay .tmp/ - saltando dedup"
fi

# Validate
if [[ $VALIDATE -eq 1 ]]; then
    run_validate "$OUTDIR"
fi

# Stats
show_stats "$OUTDIR"
