"""Sign-in is offered only when a link needs an account."""

import json

import pytest
from conftest import wait_until

import main
from main import installed_browsers, needs_sign_in, site_of

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
    return [button.cget("text") for button in app.sign_in_buttons.winfo_children()]


def test_no_sign_in_anywhere_by_default(app):
    assert app.sign_in_panel.winfo_manager() == ""
    assert app.sign_in_chip.winfo_manager() == ""


def test_panel_appears_for_a_link_that_needs_an_account(app, monkeypatch):
    monkeypatch.setattr(main, "installed_browsers", lambda: ["Chrome", "Firefox"])
    show(app, needs_account())

    assert app.sign_in_panel.winfo_manager() == "grid"
    assert app.preview_title.cget("text") == "This needs an account"
    assert "Instagram" in app.sign_in_text.cget("text")
    # Firefox first (most reliable), only installed browsers, plus a file option
    assert pill_texts(app) == ["Firefox", "Chrome", "Cookies file…"]


def test_panel_hidden_for_errors_sign_in_cannot_fix(app):
    show(app, {**needs_account(), "needs_sign_in": False, "error": "Unavailable"})
    assert app.sign_in_panel.winfo_manager() == ""


def test_signing_in_retries_the_link(app, monkeypatch):
    retried = []
    monkeypatch.setattr(main, "installed_browsers", lambda: ["Firefox"])
    monkeypatch.setattr(main, "read_logins", lambda method, file=None: ["Instagram"])
    monkeypatch.setattr(app, "start_lookup", lambda force=False: retried.append(force))

    show(app, needs_account())
    app.choose_sign_in("Firefox")

    assert wait_until(app.root, lambda: retried)
    assert app.sign_in_method == "Firefox"
    assert "Signed in with Firefox" in app.sign_in_status.cget("text")
    assert app.sign_in_chip.winfo_manager() == "pack"
    assert app.ydl_options()["cookiesfrombrowser"] == ("firefox",)

    with open(main.SETTINGS_FILE, encoding="utf-8") as file:
        assert json.load(file)["sign_in"] == "Firefox"


def test_browser_not_signed_in_to_that_site_explains_what_to_do(app, monkeypatch):
    monkeypatch.setattr(main, "read_logins", lambda method, file=None: ["YouTube"])
    show(app, needs_account())
    app.choose_sign_in("Chrome")

    assert wait_until(app.root, lambda: "✕" in app.sign_in_status.cget("text"))
    assert "Chrome isn't signed in to Instagram" in app.sign_in_status.cget("text")
    assert app.sign_in_method == main.NO_SIGN_IN  # nothing changed


def test_unreadable_browser_login_is_explained(app, monkeypatch):
    def locked(method, file=None):
        raise RuntimeError("Failed to decrypt with DPAPI")

    monkeypatch.setattr(main, "read_logins", locked)
    show(app, needs_account())
    app.choose_sign_in("Edge")

    assert wait_until(app.root, lambda: "Firefox" in app.sign_in_status.cget("text"))
    assert app.sign_in_method == main.NO_SIGN_IN


def test_still_blocked_after_signing_in_says_so(app):
    app.sign_in_method = "Firefox"
    show(app, needs_account())
    assert "can't open this" in app.sign_in_status.cget("text")


def test_bug005_panel_and_long_message_do_not_widen_the_window(app):
    """Wrap widths mixed screen pixels with scaled points, so on 125% displays
    long messages made the window wider."""

    show(app, {**needs_account(), "error": "word " * 60})
    app.set_sign_in_status("✕  " + "word " * 40, main.BAD)
    app.root.update()

    # The window is never narrower than its minimum (640 points); the content
    # must fit inside that, or the window grows
    scale = app.root._get_window_scaling()
    assert app.root.winfo_reqwidth() <= 640 * scale + 2


def test_sign_out_from_the_chip(app):
    app.sign_in_method = "Firefox"
    app.update_sign_in_chip()
    assert app.sign_in_chip.cget("text").startswith("🔒  Firefox")

    app.sign_out()
    assert app.sign_in_method == main.NO_SIGN_IN
    assert app.sign_in_chip.winfo_manager() == ""
    assert "cookiesfrombrowser" not in app.ydl_options()
