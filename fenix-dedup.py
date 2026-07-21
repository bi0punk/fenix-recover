#!/usr/bin/env python3
"""
fenix-dedup.py - Copia archivos de .tmp/ a salida única con naming único y dedup.

Toma los outputs raw de Foremost (.tmp/foremost/) y PhotoRec (.tmp/photorec/),
los copia a un directorio de salida único organizado por tipo de archivo.
Los nombres siguen el patrón F_NNNNNN.ext (Foremost) y P_NNNNNN.ext (PhotoRec).
PhotoRec verifica SHA256 contra Foremost para evitar duplicados.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"

# Extensión por MIME type (fallback)
EXTENSION_MAP = {
    "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
    "image/bmp": "bmp", "image/tiff": "tiff", "image/webp": "webp",
    "image/vnd.microsoft.icon": "ico",
    "application/pdf": "pdf",
    "application/zip": "zip", "application/x-rar-compressed": "rar",
    "application/gzip": "gz", "application/x-7z-compressed": "7z",
    "application/x-tar": "tar",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt", "text/html": "html", "text/csv": "csv",
    "video/mp4": "mp4", "video/x-msvideo": "avi", "video/quicktime": "mov",
    "video/x-matroska": "mkv",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/flac": "flac",
    "application/json": "json", "application/xml": "xml",
}


def sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def mime_type(path: Path) -> str | None:
    try:
        r = subprocess.run(["file", "--mime-type", "-b", str(path)],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    try:
        import magic
        return magic.from_file(str(path), mime=True)
    except ImportError:
        pass
    return None


def classify_ext(path: Path, type_hint: str | None = None) -> str:
    if type_hint and type_hint in EXTENSION_MAP:
        return EXTENSION_MAP[type_hint]
    if type_hint and type_hint.startswith("image/"):
        return type_hint.split("/")[-1]
    if type_hint and type_hint.startswith("video/"):
        return type_hint.split("/")[-1]
    if type_hint and type_hint.startswith("audio/"):
        return type_hint.split("/")[-1]
    s = path.suffix.lower().lstrip(".")
    if s and len(s) <= 6:
        return s
    m = mime_type(path)
    if m:
        if m in EXTENSION_MAP:
            return EXTENSION_MAP[m]
        if "/" in m:
            return m.split("/")[-1]
    return "unknown"


def init_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_hashes (
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            source TEXT NOT NULL,
            ext TEXT NOT NULL,
            PRIMARY KEY (sha256, size)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS counters (
            ext TEXT PRIMARY KEY,
            counter INTEGER NOT NULL DEFAULT 0
        )
    """)
    return conn


def next_counter(conn, ext: str) -> int:
    cur = conn.execute("SELECT counter FROM counters WHERE ext=?", (ext,))
    row = cur.fetchone()
    if row:
        c = row[0] + 1
        conn.execute("UPDATE counters SET counter=? WHERE ext=?", (c, ext))
    else:
        c = 1
        conn.execute("INSERT INTO counters (ext, counter) VALUES (?, ?)", (ext, c))
    conn.commit()
    return c


def copy_file(src: Path, dst_dir: Path, prefix: str, conn: sqlite3.Connection,
              ext_hint: str | None = None) -> tuple[str, int] | None:
    ext = classify_ext(src, ext_hint)
    ext_dir = dst_dir / ext
    ext_dir.mkdir(parents=True, exist_ok=True)

    counter = next_counter(conn, ext)
    fname = f"{prefix}_{counter:08d}.{ext}"
    dst = ext_dir / fname

    try:
        shutil.copy2(src, dst)
        return fname, counter
    except (OSError, PermissionError) as e:
        print(f"  [warn] Error copiando {src}: {e}", file=sys.stderr)
        return None


def index_foremost(conn, tmp_dir: Path, output_dir: Path):
    count = 0
    # Foremost organiza por tipo en subdirectorios
    for subdir in sorted(tmp_dir.iterdir()):
        if not subdir.is_dir() or subdir.name == "audit.txt":
            continue
        ext_hint = subdir.name
        for fpath in sorted(subdir.iterdir()):
            if not fpath.is_file():
                continue
            try:
                sha = sha256_file(fpath)
                size = fpath.stat().st_size
                conn.execute(
                    "INSERT OR IGNORE INTO file_hashes (sha256, size, source, ext) VALUES (?, ?, 'foremost', ?)",
                    (sha, size, ext_hint)
                )
                result = copy_file(fpath, output_dir, "F", conn, ext_hint)
                if result:
                    count += 1
            except (PermissionError, OSError) as e:
                print(f"  [warn] {fpath}: {e}", file=sys.stderr)
    conn.commit()
    return count


def index_photorec(conn, tmp_dir: Path, output_dir: Path):
    count = 0
    dups = 0
    recup_dirs = sorted(tmp_dir.glob("recup_dir.*"))
    if not recup_dirs:
        recup_dirs = [tmp_dir]
    for rdir in recup_dirs:
        for fpath in sorted(rdir.iterdir()):
            if not fpath.is_file():
                continue
            try:
                sha = sha256_file(fpath)
                size = fpath.stat().st_size
                cur = conn.execute(
                    "SELECT 1 FROM file_hashes WHERE sha256=? AND size=? LIMIT 1",
                    (sha, size)
                )
                if cur.fetchone():
                    dups += 1
                    continue
                conn.execute(
                    "INSERT INTO file_hashes (sha256, size, source, ext) VALUES (?, ?, 'photorec', ?)",
                    (sha, size, "unknown")
                )
                result = copy_file(fpath, output_dir, "P", conn)
                if result:
                    count += 1
            except (PermissionError, OSError) as e:
                print(f"  [warn] {fpath}: {e}", file=sys.stderr)
    conn.commit()
    return count, dups


def count_files(base: Path) -> int:
    if not base.exists():
        return 0
    return sum(1 for _ in base.rglob("*") if _.is_file())


def get_counter_from_output(output_dir: Path) -> dict:
    by_type = {}
    for subdir in output_dir.iterdir():
        if subdir.is_dir():
            by_type[subdir.name] = count_files(subdir)
    return by_type


def main():
    ap = argparse.ArgumentParser(
        description="fenix-dedup - Copia .tmp/ a salida única con naming único y dedup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--foremost-tmp", required=True, help="Directorio .tmp/foremost/")
    ap.add_argument("--photorec-tmp", required=True, help="Directorio .tmp/photorec/")
    ap.add_argument("--output", required=True, help="Directorio de salida final")
    ap.add_argument("--db", required=True, help="Archivo SQLite de hashes")
    ap.add_argument("--report", help="Archivo JSON de reporte")
    ap.add_argument("--version", "-V", action="version", version=f"fenix-dedup v{VERSION}")

    args = ap.parse_args()

    foremost_tmp = Path(args.foremost_tmp)
    photorec_tmp = Path(args.photorec_tmp)
    output_dir = Path(args.output)
    db_path = Path(args.db)

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] fenix-dedup v{VERSION}")
    print(f"    Foremost tmp: {foremost_tmp}")
    print(f"    PhotoRec tmp: {photorec_tmp}")
    print(f"    Output:       {output_dir}")
    print()

    conn = init_db(db_path)

    # Fase 1: Foremost
    print("[*] Indexando Foremost...")
    f_count = index_foremost(conn, foremost_tmp, output_dir)
    print(f"  {f_count} archivos copiados (prefijo F_)")
    print()

    # Fase 2: PhotoRec con dedup
    print("[*] Indexando PhotoRec (con dedup)...")
    p_count, dups = index_photorec(conn, photorec_tmp, output_dir)
    total_in_db = conn.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0]
    unique_hash = conn.execute("SELECT COUNT(DISTINCT sha256, size) FROM file_hashes").fetchone()[0]
    print(f"  {p_count} archivos copiados (prefijo P_)")
    print(f"  {dups} duplicados evitados")
    print()

    # Stats finales
    by_type = get_counter_from_output(output_dir)
    total_out = sum(by_type.values())

    print(f"[*] Total en {output_dir}: {total_out} archivos")
    for ext, cnt in sorted(by_type.items()):
        if ext in ("valid", "corrupt", "repaired", ".tmp"):
            continue
        print(f"    {ext}/: {cnt} archivos")

    report = {
        "tool": f"fenix-dedup v{VERSION}",
        "timestamp": datetime.now().isoformat(),
        "foremost": {"copied": f_count, "source": str(foremost_tmp)},
        "photorec": {"copied": p_count, "duplicates_skipped": dups, "source": str(photorec_tmp)},
        "total_in_db": total_in_db,
        "unique_hashes": unique_hash,
        "output": str(output_dir),
        "total_files": total_out,
        "by_type": by_type,
    }

    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        with open(rp, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n[*] Reporte: {rp}")

    conn.close()
    return report


if __name__ == "__main__":
    main()
