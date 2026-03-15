"""Tests for boletus.onboarding.file_parser — file extraction and context saving."""

import os
import pytest

from boletus.onboarding.file_parser import extract_text, save_to_context_dir, _extract_plain


def test_extract_plain_text():
    data = b"Hello world\nThis is a test"
    result = extract_text(data, "test.txt")
    assert "Hello world" in result
    assert "This is a test" in result


def test_extract_markdown():
    data = b"# Title\n\nSome content"
    result = extract_text(data, "plan.md")
    assert "Title" in result
    assert "Some content" in result


def test_extract_csv():
    data = b"name,email\nJohn,john@test.com"
    result = extract_text(data, "data.csv")
    assert "John" in result
    assert "john@test.com" in result


def test_extract_json():
    data = b'{"key": "value"}'
    result = extract_text(data, "config.json")
    assert "value" in result


def test_extract_yaml():
    data = b"key: value\nlist:\n  - item1"
    result = extract_text(data, "config.yaml")
    assert "value" in result


def test_extract_yml():
    data = b"key: value"
    result = extract_text(data, "config.yml")
    assert "value" in result


def test_extract_unsupported():
    result = extract_text(b"binary data", "image.png")
    assert result == ""


def test_extract_plain_utf8():
    assert _extract_plain("hello".encode("utf-8")) == "hello"


def test_extract_plain_latin1():
    result = _extract_plain("café".encode("latin-1"))
    assert "caf" in result


def test_extract_plain_utf8_bom():
    data = b"\xef\xbb\xbfhello bom"
    result = _extract_plain(data)
    assert "hello bom" in result


def test_save_to_context_dir(tmp_path):
    path = save_to_context_dir(str(tmp_path), "business-plan.pdf", "Extracted content here")
    assert os.path.exists(path)
    with open(path) as f:
        content = f.read()
    assert "business-plan.pdf" in content
    assert "Extracted content here" in content


def test_save_creates_directory(tmp_path):
    subdir = os.path.join(str(tmp_path), "nested", "context")
    path = save_to_context_dir(subdir, "doc.txt", "content")
    assert os.path.exists(path)


def test_save_avoids_overwrite(tmp_path):
    path1 = save_to_context_dir(str(tmp_path), "doc.pdf", "version 1")
    path2 = save_to_context_dir(str(tmp_path), "doc.pdf", "version 2")
    assert path1 != path2
    # Both files should exist
    assert os.path.exists(path1)
    assert os.path.exists(path2)


def test_save_sanitizes_filename(tmp_path):
    path = save_to_context_dir(str(tmp_path), "my weird (file) [v2].pdf", "content")
    assert os.path.exists(path)
    # Should not contain special chars in the saved filename
    basename = os.path.basename(path)
    assert "(" not in basename
    assert ")" not in basename


def test_save_output_is_markdown(tmp_path):
    path = save_to_context_dir(str(tmp_path), "notes.txt", "Some notes")
    assert path.endswith(".md")
