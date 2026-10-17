import pytest

from agent.channels.cli import parse
from agent.messages import Image, Text


def test_text():
    assert parse("  hello  ") == (Text("hello"),)


def test_blank_line_is_nothing():
    assert parse("   ") == ()


def test_image_with_text(tmp_path):
    file = tmp_path / "shot.png"
    file.write_bytes(b"png")
    assert parse(f"/image {file} what is this?") == (Image(b"png", "image/png"), Text("what is this?"))


def test_image_alone(tmp_path):
    file = tmp_path / "shot.jpg"
    file.write_bytes(b"jpg")
    assert parse(f"/image {file}") == (Image(b"jpg", "image/jpeg"),)


def test_rejects_non_images(tmp_path):
    file = tmp_path / "notes.txt"
    file.write_text("hi")
    with pytest.raises(ValueError):
        parse(f"/image {file}")


def test_missing_file(tmp_path):
    with pytest.raises(OSError):
        parse(f"/image {tmp_path / 'nope.png'}")
