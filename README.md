# fenix-recover

Super script de recuperación de datos que fusiona **Foremost** y **PhotoRec** en una sola pasada con salida única, deduplicación automática y validación de archivos.

## Cómo funciona

1. **Foremost** — carvado rápido → `.tmp/foremost/`
2. **PhotoRec** — carvado profundo → `.tmp/photorec/`
3. **Deduplicación** — Copia a `jpg/`, `pdf/`, etc. con nombres únicos (`F_NNNNNN.jpg`, `P_NNNNNN.jpg`). SHA256 evita duplicados entre ambas herramientas
4. **Validación** — Clasifica en `valid/`, `repaired/`, `corrupt/`

```
INPUT (/dev/sdb o imagen.dd)
  ├── Foremost ──→ .tmp/foremost/
  ├── PhotoRec ──→ .tmp/photorec/   (dedup automático contra Foremost)
  ├── Dedup ─────→ jpg/, pdf/, ...   (salida única: F_ + P_)
  └── Validate ──→ valid/, repaired/, corrupt/
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

# Solo validar una salida existente
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
| `--ddrescue` | Crear imagen con ddrescue antes de tallar |
| `--no-validate` | Skip validación de archivos |
| `--validate-only` | Solo validar salida existente |
| `-q` | Modo silencioso |

## Estructura de salida

```
recovery_20260721_1430/
├── jpg/                # ★ Archivos únicos (F_ de Foremost, P_ de PhotoRec)
│   ├── F_00000001.jpg
│   ├── F_00000002.jpg
│   ├── P_00000001.jpg   ← solo si PhotoRec encontró algo único
│   └── ...
├── pdf/
├── zip/
├── unknown/
├── ...                 # otros tipos según lo recuperado
│
├── valid/              # Copia de archivos que pasaron validación
│   ├── jpg/
│   ├── pdf/
│   └── ...
├── repaired/           # Archivos corruptos que se pudieron reparar
│   └── jpg/
├── corrupt/            # Archivos corruptos sin reparación
│   └── jpg/
│
├── report.json         # Stats de dedup
├── validate_report.json# Stats de validación
├── hashes.db           # SQLite con SHA256
├── disk_image.dd       # (si se usó --ddrescue)
├── ddrescue.log
└── .tmp/               # Se elimina automáticamente al final
```

## Advertencias

- Siempre recuperar a un disco **diferente** del que contiene los datos perdidos
- Los nombres originales de archivo NO se pueden recuperar (el carvado ignora el filesystem)
- Para corrupción severa, usar `--ddrescue` primero
- Este script no modificará el dispositivo de origen
