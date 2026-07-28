import hashlib
import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Cargar módulo con guion en el nombre
spec = importlib.util.spec_from_file_location(
    "fenix_dedup",
    Path(__file__).resolve().parent.parent / "fenix-dedup.py",
)
fenix_dedup = importlib.util.module_from_spec(spec)
sys.modules["fenix_dedup"] = fenix_dedup
spec.loader.exec_module(fenix_dedup)

from fenix_dedup import (  # noqa: E402
    EXTENSION_MAP,
    classify_ext,
    copy_file,
    init_db,
    next_counter,
    sha256_file,
)


def test_sha256_file():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello world")
        f.flush()
        path = Path(f.name)

    expected = hashlib.sha256(b"hello world").hexdigest()
    assert sha256_file(path) == expected
    path.unlink()


def test_classify_ext_by_suffix():
    assert classify_ext(Path("test.jpg")) == "jpg"
    assert classify_ext(Path("test.pdf")) == "pdf"
    assert classify_ext(Path("test.mp4")) == "mp4"


def test_classify_ext_unknown():
    assert classify_ext(Path("test")) == "unknown"
    assert classify_ext(Path("test.toolongext")) == "unknown"


def test_classify_ext_with_hint():
    assert classify_ext(Path("file"), "image/jpeg") == "jpg"
    assert classify_ext(Path("file"), "application/pdf") == "pdf"


def test_extension_map_has_common_types():
    assert "image/jpeg" in EXTENSION_MAP
    assert "application/pdf" in EXTENSION_MAP
    assert "video/mp4" in EXTENSION_MAP


def test_init_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    conn = init_db(db_path)
    assert conn is not None
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {t[0] for t in tables}
    assert "file_hashes" in table_names
    assert "counters" in table_names
    conn.close()
    db_path.unlink()


def test_next_counter():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    conn = init_db(db_path)
    assert next_counter(conn, "jpg") == 1
    assert next_counter(conn, "jpg") == 2
    assert next_counter(conn, "png") == 1
    conn.close()
    db_path.unlink()


def test_copy_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "source.txt"
        src.write_text("test content")
        dst = Path(tmpdir) / "out"
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        conn = init_db(db_path)
        result = copy_file(src, dst, "F", conn, "txt")
        assert result is not None
        name, counter = result
        assert name.endswith(".txt")
        assert counter == 1
        assert (dst / "txt" / name).exists()
        conn.close()
        db_path.unlink()
