#!/usr/bin/env python3
"""
fenix-dedup.py - Deduplicación y merge de resultados de Foremost + PhotoRec

SHA256 + SQLite para identificar duplicados entre ambas herramientas,
priorizando archivos de Foremost (mejor nombrados) sobre PhotoRec.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"

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
    "application/octet-stream": "bin",
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
        result = subprocess.run(
            ["file", "--mime-type", "-b", str(path)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    try:
        import magic
        return magic.from_file(str(path), mime=True)
    except ImportError:
        pass
    return None


def ext_from_mime(mime: str) -> str | None:
    if not mime:
        return None
    return EXTENSION_MAP.get(mime)


def ext_from_path(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext:
        return ext
    stem = path.stem.lower()
    for sep in ("_", "-", "."):
        parts = stem.split(sep)
        if len(parts) > 1 and parts[-1].isalnum() and len(parts[-1]) <= 5:
            return parts[-1]
    return "unknown"


def classify_by_mime(path: Path) -> str:
    mime = mime_type(path)
    if mime:
        ext = ext_from_mime(mime)
        if ext:
            return ext
        if "image/" in mime:
            return mime.split("/")[-1]
        if "video/" in mime:
            return mime.split("/")[-1]
        if "audio/" in mime:
            return mime.split("/")[-1]
        if "text/" in mime:
            return mime.split("/")[-1]
    return ext_from_path(path)


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
            path TEXT NOT NULL,
            PRIMARY KEY (sha256, size)
        )
    """)
    return conn


def index_directory(conn, base_path: Path, source: str):
    count = 0
    if not base_path.exists():
        return count
    for fpath in base_path.rglob("*"):
        if not fpath.is_file():
            continue
        try:
            size = fpath.stat().st_size
            sha = sha256_file(fpath)
            conn.execute(
                "INSERT OR IGNORE INTO file_hashes (sha256, size, source, path) VALUES (?, ?, ?, ?)",
                (sha, size, source, str(fpath.relative_to(base_path)))
            )
            count += 1
        except (PermissionError, OSError) as e:
            print(f"  [warn] No se pudo leer {fpath}: {e}", file=sys.stderr)
    conn.commit()
    return count


def copy_unique(conn, src_base: Path, dst_base: Path, preferred_source: str):
    dst_base.mkdir(parents=True, exist_ok=True)
    copied = 0
    seen = set()

    rows = conn.execute(
        "SELECT sha256, size, source, path FROM file_hashes ORDER BY "
        "CASE WHEN source = ? THEN 0 ELSE 1 END",
        (preferred_source,)
    ).fetchall()

    for sha, size, source, rel_path in rows:
        if (sha, size) in seen:
            continue
        seen.add((sha, size))

        src_path = src_base.parent / source / rel_path if source != preferred_source else None
        if src_path is None or not src_path.exists():
            src_path = src_base / source / rel_path
            if not src_path.exists():
                alt_rel = conn.execute(
                    "SELECT path FROM file_hashes WHERE sha256=? AND size=? AND source=? LIMIT 1",
                    (sha, size, preferred_source)
                ).fetchone()
                if alt_rel:
                    src_path = src_base / preferred_source / alt_rel[0]

        if not src_path.exists():
            continue

        ext = classify_by_mime(src_path)
        ext_dir = dst_base / ext
        ext_dir.mkdir(parents=True, exist_ok=True)

        counter = 1
        stem = f"{source}_{Path(rel_path).stem}"
        dst_path = ext_dir / f"{stem}.{ext}"
        while dst_path.exists():
            dst_path = ext_dir / f"{stem}_{counter}.{ext}"
            counter += 1

        try:
            import shutil
            shutil.copy2(src_path, dst_path)
            copied += 1
        except (OSError, PermissionError) as e:
            print(f"  [warn] Error copiando {src_path}: {e}", file=sys.stderr)

    return copied


def count_files(base: Path) -> int:
    if not base.exists():
        return 0
    return sum(1 for _ in base.rglob("*") if _.is_file())


def run(args):
    base_dir = Path(args.output) if args.output else Path.cwd()
    foremost_dir = Path(args.foremost) if args.foremost else base_dir / "foremost"
    photorec_dir = Path(args.photorec) if args.photorec else base_dir / "photorec"
    merged_dir = Path(args.merged) if args.merged else base_dir / "merged"
    db_path = Path(args.db) if args.db else base_dir / "hashes.db"
    report_path = Path(args.report) if args.report else base_dir / "report.json"

    print("[*] fenix-dedup v%s" % VERSION)
    print(f"    Foremost: {foremost_dir}")
    print(f"    PhotoRec: {photorec_dir}")
    print(f"    Merged:   {merged_dir}")
    print(f"    DB:       {db_path}")
    print()

    conn = init_db(db_path)

    f_count = index_directory(conn, foremost_dir, "foremost")
    print(f"  Indexados {f_count} archivos de Foremost")

    p_count = index_directory(conn, photorec_dir, "photorec")
    print(f"  Indexados {p_count} archivos de PhotoRec")

    total_indexed = conn.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0]
    unique_hashes = conn.execute("SELECT COUNT(DISTINCT sha256, size) FROM file_hashes").fetchone()[0]
    dups = total_indexed - unique_hashes

    print(f"\n  Total indexados: {total_indexed}")
    print(f"  Hashes únicos:   {unique_hashes}")
    print(f"  Duplicados:      {dups}")

    if merged_dir:
        print(f"\n[*] Copiando archivos únicos a {merged_dir}...")
        copied = copy_unique(conn, base_dir.parent if args.foremost else base_dir, merged_dir, "foremost")
        merged_count = count_files(merged_dir)
        print(f"  Archivos en merged/: {merged_count}")

    print("\n[*] Generando reporte...")
    report = {
        "tool": f"fenix-dedup v{VERSION}",
        "timestamp": datetime.now().isoformat(),
        "foremost": {
            "files": f_count,
            "source": str(foremost_dir),
        },
        "photorec": {
            "files": p_count,
            "source": str(photorec_dir),
        },
        "dedup": {
            "total_indexed": total_indexed,
            "unique": unique_hashes,
            "duplicates_removed": dups,
        },
        "merged": {
            "path": str(merged_dir),
            "files": count_files(merged_dir) if merged_dir else 0,
        },
    }

    if merged_dir and merged_dir.exists():
        by_type = {}
        for d in merged_dir.iterdir():
            if d.is_dir():
                by_type[d.name] = count_files(d)
        report["by_type"] = by_type

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Reporte: {report_path}")

    conn.close()
    return report


def main():
    ap = argparse.ArgumentParser(
        description="fenix-dedup - Deduplicación Foremost + PhotoRec",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--foremost", help="Directorio de salida de Foremost")
    ap.add_argument("--photorec", help="Directorio de salida de PhotoRec")
    ap.add_argument("--merged", help="Directorio de salida mergeada")
    ap.add_argument("--db", help="Archivo SQLite de hashes")
    ap.add_argument("--report", help="Archivo JSON de reporte")
    ap.add_argument("--output", "-o", help="Directorio base (alternativa a los paths individuales)")
    ap.add_argument("--version", "-V", action="version", version=f"fenix-dedup v{VERSION}")

    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
