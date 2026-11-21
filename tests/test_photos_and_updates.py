"""Photo posts (gallery-dl) and auto-update, with the network faked."""

import functools
import hashlib
import http.server
import io
import json
import subprocess
import threading
import urllib.error

import pytest

import core
from core import download_update, find_photos, gallery_error, post_details

# ---------- photos ----------

TIKTOK_SLIDESHOW = [
    [2, {"category": "tiktok"}],
    [
        3,
        "https://cdn/a.jpeg",
        {
            "category": "tiktok",
            "extension": "jpeg",
            "desc": "Ella trip #travel",
            "id": "7301",
            "author": {"uniqueId": "wanderer"},
        },
    ],
    [3, "https://cdn/b.jpeg", {"category": "tiktok", "extension": "jpeg"}],
    [3, "https://cdn/music.mp3", {"category": "tiktok", "extension": "mp3"}],
]


def fake_run(stdout="", returncode=0, stderr=""):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


def test_find_photos_counts_images_only(monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", fake_run(json.dumps(TIKTOK_SLIDESHOW)))
    photos, meta = find_photos("https://www.tiktok.com/@w/photo/7301", [])
    assert photos == ["https://cdn/a.jpeg", "https://cdn/b.jpeg"]
    assert post_details(meta) == {
        "site": "TikTok",
        "title": "Ella trip",
        "uploader": "wanderer",
        "id": "7301",
    }


def test_find_photos_raises_the_site_error_when_nothing_found(monkeypatch):
    stderr = "[instagram][info] ...\n[instagram][error] HttpError: '401 Unauthorized'"
    monkeypatch.setattr(core.subprocess, "run", fake_run("[]", 1, stderr))
    with pytest.raises(RuntimeError, match="401"):
        find_photos("https://www.instagram.com/p/x/", [])


def test_find_photos_survives_garbage_output(monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", fake_run("not json", 0))
    assert find_photos("https://www.instagram.com/p/x/", []) == ([], {})


def test_gallery_error_without_error_lines():
    assert gallery_error("") == "No photos were found in this post."


@pytest.mark.parametrize(
    ("meta", "uploader", "post_id"),
    [
        (
            {
                "category": "instagram",
                "username": "amy",
                "post_shortcode": "Cx1",
                "description": "Hi",
            },
            "amy",
            "Cx1",
        ),
        (
            {
                "category": "facebook",
                "user": {"username": "page"},
                "id": 9,
                "caption": "Hello",
            },
            "page",
            "9",
        ),
        ({"category": "tiktok"}, "", "post"),
    ],
)
def test_post_details_per_site(meta, uploader, post_id):
    details = post_details(meta)
    assert details["uploader"] == uploader
    assert details["id"] == post_id
    assert details["title"]  # never empty


# ---------- updates ----------


def fake_release_api(monkeypatch, payload=None, error=None):
    def urlopen(request, timeout=None):
        if error:
            raise error
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)


def test_latest_release_picks_the_setup_file(monkeypatch):
    fake_release_api(
        monkeypatch,
        {
            "tag_name": "v1.2.0",
            "assets": [
                {"name": "notes.txt", "browser_download_url": "x"},
                {
                    "name": "VideoDownloader-Setup-1.2.0.exe",
                    "browser_download_url": "u",
                    "size": 5,
                    "digest": "sha256:ABC",
                },
            ],
        },
    )
    release = core.fetch_latest_release()
    assert release == {
        "version": "1.2.0",
        "name": "VideoDownloader-Setup-1.2.0.exe",
        "url": "u",
        "size": 5,
        "digest": "sha256:abc",
    }


def test_release_without_setup_file_is_ignored(monkeypatch):
    fake_release_api(monkeypatch, {"tag_name": "v9.0.0", "assets": []})
    assert core.fetch_latest_release() is None


def test_no_releases_yet_is_not_an_error(monkeypatch):
    error = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    fake_release_api(monkeypatch, error=error)
    assert core.fetch_latest_release() is None


@pytest.fixture
def file_server(tmp_path):
    data = b"fake installer " * 50_000
    (tmp_path / "VideoDownloader-Setup-1.1.0.exe").write_bytes(data)

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)
    )
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    yield data, f"http://127.0.0.1:{server.server_port}/VideoDownloader-Setup-1.1.0.exe"
    server.shutdown()


def release_for(data, url, digest=None):
    return {
        "version": "1.1.0",
        "name": "VideoDownloader-Setup-1.1.0.exe",
        "url": url,
        "size": len(data),
        "digest": digest or "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def test_update_download_ok(file_server):
    data, url = file_server
    steps = []
    path = download_update(release_for(data, url), lambda d, t: steps.append(d))
    with open(path, "rb") as file:
        assert file.read() == data
    assert steps[-1] == len(data)


def test_damaged_update_is_rejected(file_server):
    data, url = file_server
    with pytest.raises(RuntimeError, match="damaged"):
        download_update(release_for(data, url, "sha256:" + "0" * 64), lambda d, t: None)
