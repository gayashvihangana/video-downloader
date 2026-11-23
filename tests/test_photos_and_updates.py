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
            "published_at": "1970-01-02T00:00:00Z",
            "body": "Fixes.\n<!-- minimum-version: 1.1.0 -->",
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
        "published": 86400,
        "required": False,
        "minimum": "1.1.0",
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


# ---------- mandatory updates (optional for a while, then required) ----------

DAY = 86400
NOW = 1_800_000_000


def release(version="2.1.0", days_old=0.0, **extra):
    return {"version": version, "published": NOW - days_old * DAY, **extra}


def policy(rel, current="2.0.0"):
    return core.update_policy(rel, current=current, now=NOW)


def test_no_policy_when_already_up_to_date():
    assert policy(release("2.0.0")) is None
    assert policy(release("1.9.9")) is None
    assert policy(None) is None


@pytest.mark.parametrize(
    ("days_old", "required", "days_left"),
    [
        (0, False, 7),
        (0.5, False, 7),
        (3, False, 4),
        (6.2, False, 1),
        (7, True, 0),
        (30, True, 0),
    ],
)
def test_optional_during_the_grace_period_then_required(days_old, required, days_left):
    assert policy(release(days_old=days_old)) == {
        "required": required,
        "days_left": days_left,
    }


def test_a_release_can_be_required_at_once():
    assert policy(release(required=True))["required"] is True


def test_minimum_version_requires_only_older_versions():
    rel = release("2.3.0", minimum="2.2.0")
    assert policy(rel, current="2.1.0")["required"] is True
    assert policy(rel, current="2.2.0")["required"] is False


def test_unknown_publish_date_starts_the_grace_period_now():
    assert policy({"version": "2.1.0"}) == {"required": False, "days_left": 7}


def test_rules_are_read_from_hidden_lines_in_the_release_notes():
    notes = "Fixed YouTube downloads.\n\n<!-- update: required -->\n"
    assert core.release_rules(notes) == (True, None)

    notes = "New look.\n<!-- minimum-version: v2.1.0 -->"
    assert core.release_rules(notes) == (False, "2.1.0")

    assert core.release_rules("Just notes. update: required") == (False, None)
    assert core.release_rules(None) == (False, None)


def test_github_dates():
    assert core.github_time("1970-01-02T00:00:00Z") == DAY
    assert core.github_time(None) is None
    assert core.github_time("yesterday") is None
