import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "fenix_validate",
    Path(__file__).resolve().parent.parent / "fenix-validate.py",
)
assert spec is not None and spec.loader is not None
fenix_validate = importlib.util.module_from_spec(spec)
sys.modules["fenix_validate"] = fenix_validate
spec.loader.exec_module(fenix_validate)

from fenix_validate import (  # noqa: E402
    HAS_PIL,
    SKIP_DIRS,
    _validate_image_with_pil,
    validate_jpeg,
    validate_png,
    validate_txt,
    validate_zip,
)


@pytest.fixture
def temp_txt():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        f.write("hello world\n")
        f.flush()
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def temp_png():
    if not HAS_PIL:
        pytest.skip("Pillow not available")
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img = Image.new("RGB", (10, 10), color="red")
        img.save(f, format="PNG")
        f.flush()
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def temp_jpg():
    if not HAS_PIL:
        pytest.skip("Pillow not available")
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(f, format="JPEG")
        f.flush()
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


def test_validate_txt_valid(temp_txt):
    ok, err = validate_txt(temp_txt)
    assert ok is True
    assert err is None


def test_validate_txt_empty():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"")
        f.flush()
        path = Path(f.name)
    ok, err = validate_txt(path)
    assert ok is True
    path.unlink()


def test_validate_png(temp_png):
    ok, err = validate_png(temp_png)
    assert ok is True


def test_validate_jpg(temp_jpg):
    ok, err = validate_jpeg(temp_jpg)
    assert ok is True


def test_validate_zip():
    import zipfile

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("test.txt", "content")
        f.flush()
        path = Path(f.name)
    ok, err = validate_zip(path)
    assert ok is True
    path.unlink()


def test_validate_zip_corrupt():
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(b"not a zip file")
        f.flush()
        path = Path(f.name)
    ok, _ = validate_zip(path)
    assert ok is False
    path.unlink()


def test_pil_helper_valid(temp_png):
    ok, err = _validate_image_with_pil(temp_png)
    assert ok is True


def test_pil_helper_invalid():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"not an image")
        f.flush()
        path = Path(f.name)
    ok, err = _validate_image_with_pil(path)
    assert ok is False
    path.unlink()


def test_skip_dirs_contains_expected():
    assert "valid" in SKIP_DIRS
    assert "repaired" in SKIP_DIRS
    assert "corrupt" in SKIP_DIRS
