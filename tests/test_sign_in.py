"""Sign-in is offered only when a link needs an account."""

import json

import pytest
from conftest import shown, wait_until

import core
from core import installed_browsers, needs_sign_in, site_of

# ---------- unit ----------


@pytest.mark.parametrize(
    "error",
    [
        "ERROR: [youtube] x: Private video. Sign in if you've been granted access",
        "ERROR: [youtube] x: Sign in to confirm your age",
        "ERROR: [youtube] x: Sign in to confirm you're not a bot",
        "ERROR: [instagram] x: Requested content is not available, login required",
        "[instagram][error] HttpError: '401 Unauthorized'",
        "ERROR: [youtube] x: Join this channel to get access to members-only content",
        "ERROR: [Instagram] x: empty media response",
    ],
)
def test_errors_that_need_an_account(error):
    assert needs_sign_in(error)


@pytest.mark.parametrize(
    "error",
    [
        "ERROR: Video unavailable",
        "ERROR: Unsupported URL: https://example.com",
        "<urlopen error [Errno 11001] getaddrinfo failed>",
        "[Errno 28] No space left on device",
    ],
)
def test_errors_where_signing_in_would_not_help(error):
    assert not needs_sign_in(error)


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://www.youtube.com/watch?v=x", "YouTube"),
        ("https://youtu.be/x", "YouTube"),
        ("https://www.instagram.com/p/x/", "Instagram"),
        ("https://fb.watch/abc", "Facebook"),
        ("https://www.tiktok.com/@a/video/1", "TikTok"),
        ("https://vimeo.com/1", None),
        ("", None),
    ],
)
def test_site_of(url, site):
    assert site_of(url) == site


def test_installed_browsers_finds_only_existing_profiles(tmp_path, monkeypatch):
    (tmp_path / "roaming" / "Mozilla" / "Firefox" / "Profiles").mkdir(parents=True)
    (tmp_path / "local" / "Microsoft" / "Edge" / "User Data").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert installed_browsers() == ["Firefox", "Edge"]


# ---------- UI ----------


def needs_account(url="https://www.instagram.com/p/x/"):
    return {
        "url": url,
        "title": "",
        "site": "",
        "uploader": "",
        "duration": 0,
        "qualities": [],
        "has_video": False,
        "has_audio": False,
        "videos": 0,
        "photos": 0,
        "thumbnail_url": None,
        "thumbnail": None,
        "error": "Instagram only shows this to signed-in users.",
        "needs_sign_in": True,
    }


def show(app, result):
    app.lookup_request += 1
    app.show_lookup(result, app.lookup_request)


def pill_texts(app):
    return app.sign_in_panel.button_texts()


def test_no_sign_in_anywhere_by_default(app):
    assert not shown(app.sign_in_panel)
    assert not shown(app.sign_in_chip)


def test_panel_appears_for_a_link_that_needs_an_account(app, monkeypatch):
    monkeypatch.setattr(core, "installed_browsers", lambda: ["Chrome", "Firefox"])
    show(app, needs_account())

    assert shown(app.sign_in_panel)
    assert app.preview_title.text() == "This needs an account"
    assert "Instagram" in app.sign_in_text.text()
    # Firefox first (most reliable), only installed browsers, plus a file option
    assert pill_texts(app) == ["Firefox", "Chrome", "Cookies file…"]


def test_panel_hidden_for_errors_sign_in_cannot_fix(app):
    show(app, {**needs_account(), "needs_sign_in": False, "error": "Unavailable"})
    assert not shown(app.sign_in_panel)


def test_signing_in_retries_the_link(app, monkeypatch):
    retried = []
    monkeypatch.setattr(core, "installed_browsers", lambda: ["Firefox"])
    monkeypatch.setattr(core, "read_logins", lambda method, file=None: ["Instagram"])
    monkeypatch.setattr(app, "start_lookup", lambda force=False: retried.append(force))

    show(app, needs_account())
    app.choose_sign_in("Firefox")

    assert wait_until(lambda: retried)
    assert app.sign_in_method == "Firefox"
    assert "Signed in with Firefox" in app.sign_in_status.text()
    assert shown(app.sign_in_chip)
    assert app.ydl_options()["cookiesfrombrowser"] == ("firefox",)

    with open(core.SETTINGS_FILE, encoding="utf-8") as file:
        assert json.load(file)["sign_in"] == "Firefox"


def test_browser_not_signed_in_to_that_site_explains_what_to_do(app, monkeypatch):
    monkeypatch.setattr(core, "read_logins", lambda method, file=None: ["YouTube"])
    show(app, needs_account())
    app.choose_sign_in("Chrome")

    assert wait_until(lambda: "✕" in app.sign_in_status.text())
    assert "Chrome isn't signed in to Instagram" in app.sign_in_status.text()
    assert app.sign_in_method == core.NO_SIGN_IN  # nothing changed


def test_unreadable_browser_login_is_explained(app, monkeypatch):
    def locked(method, file=None):
        raise RuntimeError("Failed to decrypt with DPAPI")

    monkeypatch.setattr(core, "read_logins", locked)
    show(app, needs_account())
    app.choose_sign_in("Edge")

    assert wait_until(lambda: "Firefox" in app.sign_in_status.text())
    assert app.sign_in_method == core.NO_SIGN_IN


def test_still_blocked_after_signing_in_says_so(app):
    app.sign_in_method = "Firefox"
    show(app, needs_account())
    assert "can't open this" in app.sign_in_status.text()


def test_sign_out_from_the_chip(app):
    app.sign_in_method = "Firefox"
    app.update_sign_in_chip()
    assert app.sign_in_chip.text() == "Signed in · Firefox"

    app.sign_out()
    assert app.sign_in_method == core.NO_SIGN_IN
    assert not shown(app.sign_in_chip)
    assert "cookiesfrombrowser" not in app.ydl_options()
