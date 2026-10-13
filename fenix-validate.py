#!/usr/bin/env python3
"""
fenix-validate.py - Validación y reparación de archivos recuperados.

Analiza archivos en busca de corrupción, intenta reparar formatos
comunes (JPEG truncado, MP4, ZIP) y clasifica el resultado.
"""

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import zipfile
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
_VERSION_PATH = Path(__file__).resolve().parent / "VERSION"
if _VERSION_PATH.exists():
    VERSION = _VERSION_PATH.read_text().strip()

HAS_PIL = False
try:
    from PIL import Image, ImageFile

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    HAS_PIL = True
except ImportError:
    pass

# ─── helpers ────────────────────────────────────────────────────────────


def run(cmd, timeout=30):
    try:
        r = subprocess.run(  # nosec B603, B607
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", "not found"
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"


def mime_type(path):
    rc, out, _ = run(["file", "--mime-type", "-b", str(path)])
    return out if rc == 0 else None


def _validate_image_with_pil(path):
    try:
        img = Image.open(path)
        img.verify()
        img.close()
        return True, None
    except Exception as e:
        return False, str(e)


def _validate_by_magic(path, keywords):
    rc, out, _ = run(["file", str(path)])
    if any(kw in out for kw in keywords):
        return True, None
    return False, out or "invalid"


# ─── validators ─────────────────────────────────────────────────────────


def validate_jpeg_pil(path):
    try:
        img = Image.open(path)
        img.verify()
        img.close()
        img = Image.open(path)
        img.load()
        img.close()
        return True, None
    except Exception as e:
        return False, str(e)


def validate_jpeg_jpeginfo(path):
    rc, out, _ = run(["jpeginfo", "-c", str(path)])
    if rc == 0:
        return True, None
    return False, out or "jpeginfo error"


def validate_jpeg(path):
    if HAS_PIL:
        ok, err = validate_jpeg_pil(path)
        if ok:
            return True, None
    rc, _, _ = run(["identify", str(path)])
    if rc == 0:
        return True, None
    rc, out, _ = run(["file", str(path)])
    if "JPEG" in out or "JFIF" in out:
        return True, None
    if HAS_PIL:
        return False, err
    return False, "unable to validate"


def repair_jpeg(path):
    data = path.read_bytes()
    eoi = b"\xff\xd9"

    candidates = []
    pos = data.rfind(eoi)
    while pos > 0:
        candidates.append(pos + 2)
        if len(candidates) >= 3:
            break
        pos = data[:pos].rfind(eoi)

    for end in candidates:
        chunk = data[:end]
        tmp = path.with_suffix(".tmp_check")
        try:
            tmp.write_bytes(chunk)
            if HAS_PIL:
                img = Image.open(tmp)
                img.verify()
                img.close()
                img = Image.open(tmp)
                img.load()
                img.close()
                tmp.unlink()
                return chunk, f"truncated at offset {end}"
            rc, _, _ = run(["identify", str(tmp)])
            tmp.unlink()
            if rc == 0:
                return chunk, f"truncated at offset {end}"
        except Exception:
            if tmp.exists():
                tmp.unlink()
            continue

    return None, None


def validate_png(path):
    if HAS_PIL:
        ok, err = _validate_image_with_pil(path)
        if ok:
            return True, None
    rc, out, _ = run(["pngcheck", str(path)])
    if rc == 0:
        return True, None
    rc, out, _ = run(["identify", str(path)])
    if rc == 0:
        return True, None
    return False, out or "invalid PNG"


def validate_gif(path):
    if HAS_PIL:
        ok, err = _validate_image_with_pil(path)
        if ok:
            return True, None
    rc, out, _ = run(["identify", str(path)])
    if rc == 0:
        return True, None
    return _validate_by_magic(path, ("GIF",))


def validate_bmp(path):
    if HAS_PIL:
        ok, err = _validate_image_with_pil(path)
        if ok:
            return True, None
    return _validate_by_magic(path, ("BMP", "bitmap"))


def validate_tiff(path):
    if HAS_PIL:
        ok, err = _validate_image_with_pil(path)
        if ok:
            return True, None
    return _validate_by_magic(path, ("TIFF",))


def validate_image_generic(path):
    if HAS_PIL:
        ok, err = _validate_image_with_pil(path)
        if ok:
            return True, None
    rc, out, _ = run(["identify", str(path)])
    if rc == 0:
        return True, None
    return False, out or "invalid image"


def validate_mp4(path):
    rc, _, err = run(["ffprobe", "-v", "error", str(path)])
    if rc == 0:
        return True, None
    return False, err or "ffprobe error"


def repair_mp4(path):
    repaired = path.parent / f"{path.stem}_repaired.mp4"
    rc, _, err = run(["ffmpeg", "-y", "-i", str(path), "-c", "copy", str(repaired)], timeout=120)
    if rc == 0 and repaired.exists():
        data = repaired.read_bytes()
        repaired.unlink()
        return data, "ffmpeg remux"
    if repaired.exists():
        repaired.unlink()
    return None, None


def validate_pdf(path):
    rc, out, _ = run(["file", str(path)])
    if "PDF" in out or "pdf" in out:
        return True, None
    data = path.read_bytes()
    if data.startswith(b"%PDF"):
        return True, None
    return False, "not a PDF"


def validate_zip(path):
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                return False, f"corrupt member: {bad}"
            return True, None
    except (zipfile.BadZipFile, OSError) as e:
        return False, str(e)


def repair_zip(path):
    tmp = path.with_suffix(".zip_fixed")
    try:
        rc, _, _ = run(["zip", "-F", str(path), "--out", str(tmp)])
        if rc == 0 and tmp.exists():
            data = tmp.read_bytes()
            tmp.unlink()
            return data, "zip -F repair"
    except Exception:  # nosec B110
        pass
    if tmp.exists():
        tmp.unlink()
    return None, None


def validate_rar(path):
    rc, _, err = run(["unrar", "t", str(path)])
    if rc == 0:
        return True, None
    return False, err or "unrar error"


def validate_mp3(path):
    return _validate_by_magic(path, ("Audio", "MPEG", "MP3"))


def validate_doc(path):
    rc, out, _ = run(["file", str(path)])
    if (
        "Composite Document File" in out
        or "Microsoft Word" in out
        or "OLE2" in out
        or "Office" in out
        or "Zip" in out
    ):
        return True, None
    ext = path.suffix.lower()
    office_exts = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
    if ext in office_exts:
        return True, None
    return False, out or "unknown document format"


def validate_txt(path):
    try:
        data = path.read_bytes()
        data.decode("utf-8")
        return True, None
    except (UnicodeDecodeError, ValueError):
        pass
    try:
        data = path.read_bytes()
        data.decode("latin-1")
        return True, None
    except Exception:  # nosec B110
        pass
    rc, out, _ = run(["file", str(path)])
    if "text" in out:
        return True, None
    return False, "not valid text"


# ─── dispatch ───────────────────────────────────────────────────────────

VALIDATORS = {
    ".jpg": (validate_jpeg, repair_jpeg),
    ".jpeg": (validate_jpeg, repair_jpeg),
    ".jfif": (validate_jpeg, repair_jpeg),
    ".png": (validate_png, None),
    ".gif": (validate_gif, None),
    ".bmp": (validate_bmp, None),
    ".tif": (validate_tiff, None),
    ".tiff": (validate_tiff, None),
    ".webp": (validate_image_generic, None),
    ".ico": (validate_image_generic, None),
    ".mp4": (validate_mp4, repair_mp4),
    ".mov": (validate_mp4, repair_mp4),
    ".avi": (validate_mp4, repair_mp4),
    ".mkv": (validate_mp4, repair_mp4),
    ".pdf": (validate_pdf, None),
    ".zip": (validate_zip, repair_zip),
    ".jar": (validate_zip, repair_zip),
    ".docx": (validate_zip, repair_zip),
    ".xlsx": (validate_zip, repair_zip),
    ".pptx": (validate_zip, repair_zip),
    ".rar": (validate_rar, None),
    ".mp3": (validate_mp3, None),
    ".wav": (validate_mp3, None),
    ".doc": (validate_doc, None),
    ".xls": (validate_doc, None),
    ".ppt": (validate_doc, None),
    ".txt": (validate_txt, None),
    ".htm": (validate_txt, None),
    ".html": (validate_txt, None),
    ".xml": (validate_txt, None),
    ".csv": (validate_txt, None),
    ".json": (validate_txt, None),
}


def validate(path, repair_dir=None):
    ext = path.suffix.lower()
    validator, repair_fn = VALIDATORS.get(ext, (None, None))

    if validator is None:
        mime = mime_type(path)
        if mime and mime.startswith("image/"):
            validator, repair_fn = (validate_image_generic, None)

    if validator is None:
        return "unchecked", None

    ok, err = validator(path)
    if ok:
        return "valid", None

    if repair_fn and repair_dir:
        data, method = repair_fn(path)
        if data:
            repair_dir.mkdir(parents=True, exist_ok=True)
            dst = repair_dir / path.name
            counter = 1
            while dst.exists():
                dst = repair_dir / f"{path.stem}_repaired{counter}{path.suffix}"
                counter += 1
            dst.write_bytes(data)
            mtime = path.stat().st_mtime
            os.utime(dst, (mtime, mtime))
            return "repaired", method
        return "corrupt", err

    return "corrupt", err


# ─── main ───────────────────────────────────────────────────────────────

SKIP_DIRS = {"valid", "repaired", "corrupt", ".tmp"}


def scan(input_dir, valid_dir, corrupt_dir, repair_dir, no_move):
    total = valid_cnt = corrupt_cnt = repaired_cnt = unchecked_cnt = 0
    by_type = {}

    files = []
    for sub in input_dir.iterdir():
        if not sub.is_dir() or sub.name in SKIP_DIRS:
            continue
        for fpath in sorted(sub.rglob("*")):
            if fpath.is_file():
                files.append(fpath)

    print(f"[*] fenix-validate v{VERSION}")
    print(f"[*] Analizando {len(files)} archivos en {input_dir}")
    print()

    for fpath in files:
        total += 1
        rel = fpath.relative_to(input_dir)
        status, info = validate(fpath, repair_dir if not no_move else None)

        # Obtener el tipo (subdirectorio padre, ej: jpg, pdf)
        try:
            file_type = rel.parts[0] if len(rel.parts) > 0 else "unknown"
        except IndexError:
            file_type = "unknown"

        by_type.setdefault(
            file_type, {"total": 0, "valid": 0, "corrupt": 0, "repaired": 0, "unchecked": 0}
        )
        by_type[file_type][status] = by_type[file_type].get(status, 0) + 1
        by_type[file_type]["total"] += 1

        if status == "valid":
            valid_cnt += 1
            if not no_move and valid_dir:
                dst = valid_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fpath, dst)
        elif status == "repaired":
            repaired_cnt += 1
        elif status == "corrupt":
            corrupt_cnt += 1
            if not no_move and corrupt_dir:
                dst = corrupt_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fpath, dst)
        else:
            unchecked_cnt += 1

        label = {"valid": "OK", "corrupt": "XX", "repaired": "RR", "unchecked": "??"}.get(
            status, "??"
        )
        info_str = f" ({info})" if info else ""
        print(f"  [{label}] {rel}{info_str}")

    print()
    print(f"  Total:    {total}")
    print(f"  Válidos:  {valid_cnt}")
    print(f"  Corruptos:{corrupt_cnt}")
    print(f"  Reparados:{repaired_cnt}")
    print(f"  No rev.:  {unchecked_cnt}")

    report = {
        "tool": f"fenix-validate v{VERSION}",
        "timestamp": datetime.now().isoformat(),
        "input": str(input_dir),
        "total": total,
        "valid": valid_cnt,
        "corrupt": corrupt_cnt,
        "repaired": repaired_cnt,
        "unchecked": unchecked_cnt,
        "by_type": by_type,
        "pillow_available": HAS_PIL,
    }
    return report


def main():
    ap = argparse.ArgumentParser(
        description="fenix-validate - Validación y reparación de archivos recuperados",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--input", "-i", required=True, help="Directorio con archivos a validar (merged/)"
    )
    ap.add_argument("--valid", help="Directorio para copias válidas")
    ap.add_argument("--corrupt", help="Directorio para copias corruptas")
    ap.add_argument("--repair", help="Directorio para archivos reparados")
    ap.add_argument("--report", help="Archivo JSON de reporte")
    ap.add_argument("--no-move", action="store_true", help="No mover archivos, solo clasificar")
    ap.add_argument("--version", "-V", action="version", version=f"fenix-validate v{VERSION}")

    args = ap.parse_args()

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        ap.error(f"Input no existe: {input_dir}")

    valid_dir = Path(args.valid) if args.valid else input_dir / "valid"
    corrupt_dir = Path(args.corrupt) if args.corrupt else input_dir / "corrupt"
    repair_dir = Path(args.repair) if args.repair else input_dir / "repaired"

    report = scan(input_dir, valid_dir, corrupt_dir, repair_dir, args.no_move)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n[*] Reporte: {report_path}")


if __name__ == "__main__":
    main()
