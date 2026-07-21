# fenix-recover

Super script de recuperación de datos que fusiona **Foremost** y **PhotoRec** con deduplicación inteligente y validación de archivos recuperados.

## Cómo funciona

1. **Foremost** — carvado rápido por headers/footers, organiza por tipo
2. **PhotoRec** — carvado profundo (300+ formatos, soporte de fragmentación)
3. **Deduplicación** — SHA256 + SQLite, prioriza archivos de Foremost, merge unificado por tipo
4. **Validación** — Analiza cada archivo, detecta corruptos, repara JPEGs truncados y MP4s dañados

```
INPUT (/dev/sdb o imagen.dd)
  ├── Foremost ──→ foremost/ (jpg/, pdf/, ...)
  ├── PhotoRec ──→ photorec/ (recup_dir.*/)
  ├── Dedup ─────→ merged/ (jpg/, pdf/, ..., único por hash)
  └── Validate ──→ merged/valid/ + merged/repaired/ + merged/corrupt/
```

## Requisitos

- Linux (Debian/Ubuntu, Arch, etc.)
- `foremost`, `photorec` (testdisk), `python3`
- `ddrescue` (opcional, para crear imagen antes de tallar)

```bash
sudo apt-get install foremost testdisk python3 ddrescue
```

## Uso

```bash
# Modo interactivo (elige dispositivo de un menú)
sudo ./fenix-recover.sh

# Batch con dispositivo
sudo ./fenix-recover.sh -i /dev/sdb -o ~/recuperado

# Batch con imagen .dd
./fenix-recover.sh -i imagen.dd -o ~/recuperado

# Solo ciertos tipos de archivo
sudo ./fenix-recover.sh -i /dev/sdb -o ~/recuperado -t jpg,pdf,zip

# Crear imagen con ddrescue primero (seguro)
sudo ./fenix-recover.sh -i /dev/sdb --ddrescue

# Solo deduplicar (si ya corriste ambas herramientas)
./fenix-recover.sh --merge-only -o ~/recuperado

# Solo validar un merged/ existente
./fenix-recover.sh --validate-only -o ~/recuperado
```

## Flags

| Flag | Descripción |
|------|-------------|
| `-i` | Dispositivo (`/dev/sdb`) o imagen (`imagen.dd`) |
| `-o` | Directorio de salida |
| `-t` | Tipos de archivo separados por coma (default: todos) |
| `--foremost-only` | Solo ejecutar Foremost |
| `--photorec-only` | Solo ejecutar PhotoRec |
| `--merge-only` | Solo deduplicar (ya existen foremost/ y photorec/) |
| `--ddrescue` | Crear imagen con ddrescue antes de tallar |
| `--no-dedup` | No deduplicar, dejar outputs raw |
| `--no-validate` | Skip validación de archivos |
| `--validate-only` | Solo validar merged/ existente |
| `-q` | Modo silencioso |

## Estructura de salida

```
recovery_20260721_1430/
├── foremost/          # Output raw de Foremost
│   ├── audit.txt
│   ├── jpg/
│   ├── pdf/
│   └── ...
├── photorec/          # Output raw de PhotoRec
│   ├── recup_dir.1/
│   ├── recup_dir.2/
│   └── report.xml
├── merged/            # ★ Resultado final deduplicado
│   ├── jpg/
│   ├── pdf/
│   ├── zip/
│   ├── unknown/
│   ├── valid/         # Archivos que pasaron validación
│   ├── repaired/      # Archivos corruptos reparados
│   └── corrupt/       # Archivos corruptos no recuperables
├── report.json        # Stats del proceso (dedup)
├── validate_report.json # Stats de validación
├── hashes.db          # SQLite con SHA256
├── audit.log          # Log completo
└── disk_image.dd      # (si se usó --ddrescue)
```

## Advertencias

- Siempre recuperar a un disco **diferente** del que contiene los datos perdidos
- Los nombres originales de archivo NO se pueden recuperar (el carvado ignora el filesystem)
- Para corrupción severa, usar `--ddrescue` primero
- Este script no modificará el dispositivo de origen
