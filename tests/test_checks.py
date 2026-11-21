"""Checks done before a download starts (error prevention)."""

import os

import pytest

import core
from core import check_folder, check_links, parse_version, retry_options, safe_name


def test_check_links_accepts_links_and_reports_other_lines():
    links, problems = check_links(
        ["https://youtu.be/a", "hello", "www.tiktok.com/@x/video/1", "ftp://x"]
    )
    assert links == ["https://youtu.be/a", "www.tiktok.com/@x/video/1"]
    assert problems == [
        'Line 2 isn\'t a link: "hello"',
        'Line 4 isn\'t a link: "ftp://x"',
    ]


def test_check_links_drops_duplicates_but_keeps_order():
    links, problems = check_links(["https://a", "https://b", "https://a"])
    assert links == ["https://a", "https://b"]
    assert problems == []


def test_check_links_shortens_long_bad_lines():
    _, [problem] = check_links(["x" * 100])
    assert problem.endswith('…"')
    assert len(problem) < 80


def test_check_folder_ok(tmp_path):
    assert check_folder(str(tmp_path / "new" / "folder")) is None
    assert os.path.isdir(tmp_path / "new" / "folder")


def test_check_folder_reports_a_file_in_the_way(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    assert "Can't create" in check_folder(str(blocker / "sub"))


class FakeUsage:
    def __init__(self, free):
        self.free = free


def test_check_folder_reports_not_enough_space(tmp_path, monkeypatch):
    monkeypatch.setattr(core.shutil, "disk_usage", lambda path: FakeUsage(1_000_000))
    problem = check_folder(str(tmp_path), needed_bytes=50_000_000)
    assert "Not enough disk space" in problem
    assert "50 MB" in problem


def test_check_folder_space_boundary(tmp_path, monkeypatch):
    # needs 100 MB (+5% headroom = 105 MB)
    usage = FakeUsage(105_000_000)
    monkeypatch.setattr(core.shutil, "disk_usage", lambda path: usage)
    assert check_folder(str(tmp_path), needed_bytes=100_000_000) is None

    usage.free = 104_999_999
    assert check_folder(str(tmp_path), needed_bytes=100_000_000) is not None


@pytest.mark.parametrize(("retries", "extractor"), [(0, 0), (3, 3), (10, 3)])
def test_retry_options(retries, extractor):
    options = retry_options(retries)
    assert options["retries"] == retries
    assert options["fragment_retries"] == retries
    assert options["extractor_retries"] == extractor
    assert options["socket_timeout"] > 0


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ('Weekend: "Ella"? <trip>', "Weekend Ella trip"),
        ("   ...", "download"),
        ("a" * 200, "a" * 80),
        ("C:\\bad/name|*", "Cbadname"),
    ],
)
def test_safe_name(name, expected):
    assert safe_name(name) == expected


@pytest.mark.parametrize(
    ("newer", "older"),
    [("1.10.0", "1.9.9"), ("v2.0", "1.99.99"), ("1.0.1", "1.0.0"), ("1.1", "1.0.9")],
)
def test_parse_version_orders_correctly(newer, older):
    assert parse_version(newer) > parse_version(older)


def test_parse_version_same_version_is_not_newer():
    assert not parse_version("v1.0.0") > parse_version("1.0.0")
