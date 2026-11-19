"""The real window (hidden), driven like a user would, with downloads faked."""

import gc
import json
import threading
import time

from conftest import pump, wait_until
from PIL import Image

import main


def lookup_result(url, title, thumbnail=True, **extra):
    return {
        "url": url,
        "title": title,
        "site": "YouTube",
        "uploader": "someone",
        "duration": 19,
        "qualities": [
            {"label": "720p", "height": 720, "size": 9_000_000, "approx": False},
            {"label": "360p", "height": 360, "size": 3_000_000, "approx": True},
        ],
        "has_video": True,
        "has_audio": True,
        "videos": 1,
        "photos": 0,
        "thumbnail_url": "x",
        "thumbnail": Image.new("RGB", (120, 68), "red") if thumbnail else None,
        "error": None,
        **extra,
    }


def show(app, result):
    """What happens when a lookup finishes."""

    app.lookup_request += 1
    app.show_loading_preview()
    app.show_lookup(result, app.lookup_request)
    gc.collect()  # throw away old pictures, like Python eventually does
    pump(app.root, 0.05)


# ---------- BUG-001 regression ----------


def test_bug001_second_link_after_a_thumbnail_shows_its_preview(app):
    """Download/cancel, then paste another link: preview got stuck on
    "Looking up…" because the old thumbnail was deleted but still in use."""

    show(app, lookup_result("https://a", "First video"))
    show(app, lookup_result("https://b", "Second video"))
    show(app, lookup_result("https://c", "Third video"))

    assert app.preview_title.cget("text") == "Third video"
    assert app.thumbnail_image is not None


def test_error_after_a_thumbnail_does_not_get_stuck(app):
    show(app, lookup_result("https://a", "First video"))
    show(app, lookup_result("https://b", "", error="This video is private."))

    assert app.preview_title.cget("text") == "Couldn't open this link"
    assert "private" in app.preview_message.cget("text")


def test_a_crash_while_drawing_the_preview_shows_a_message(app, monkeypatch):
    def broken(result):
        raise ValueError("boom")

    monkeypatch.setattr(app, "fill_preview", broken)
    app.lookup_request += 1
    app.show_lookup(lookup_result("https://a", "x"), app.lookup_request)

    assert app.preview_title.cget("text") == "Couldn't show this link"


def test_old_lookup_answers_are_ignored(app):
    app.lookup_request = 5
    app.show_lookup(lookup_result("https://old", "Old video"), 4)
    assert app.preview_title.cget("text") != "Old video"


# ---------- choices offered ----------


def test_quality_menu_lists_sizes_and_remembers_height(app):
    show(app, lookup_result("https://a", "A"))
    assert app.quality.cget("values") == ["720p  ·  9.0 MB", "360p  ·  ~3.0 MB"]

    app.quality.set("360p  ·  ~3.0 MB")
    app.on_quality_change("360p  ·  ~3.0 MB")  # what picking it in the menu does
    show(app, lookup_result("https://b", "B"))
    assert app.quality.get() == "360p  ·  ~3.0 MB"  # same height kept


def test_only_available_modes_are_offered(app):
    show(
        app,
        lookup_result(
            "https://a", "Slideshow", has_video=False, qualities=[], photos=4
        ),
    )
    assert app.mode.cget("values") == ["Audio", "Photos"]
    assert app.preview_badges.cget("text").count("photos") == 1


def test_download_button_disabled_without_links(app):
    app.clear_links()
    pump(app.root, 0.1)
    assert app.main_button.cget("state") == "disabled"


# ---------- checks before downloading ----------


def test_bad_lines_are_reported_and_nothing_starts(app):
    app.url_box.insert("1.0", "https://youtu.be/a\nnot a link")
    app.start_download()
    assert not app.is_downloading
    assert "Line 2 isn't a link" in app.status_label.cget("text")


def test_mp3_without_ffmpeg_is_explained(app):
    app.has_ffmpeg = False
    app.set_modes(["Video", "Audio"])
    app.mode.set("Audio")
    app.url_box.insert("1.0", "https://youtu.be/a")
    app.start_download()
    assert not app.is_downloading
    assert "ffmpeg" in app.status_label.cget("text")


# ---------- downloading (job faked) ----------


def run_downloads(app, links, outcomes, stop=False):
    """Run the real download loop with a fake job: outcomes[link] = None (ok)
    or an error message."""

    def job(url, options):
        if outcomes.get(url):
            raise RuntimeError(outcomes[url])
        return 1

    app.is_downloading = True
    app.download_all(links, job, {}, "file", stop)

    # Wait for the result, not a fixed time (fixed sleeps make flaky tests)
    assert wait_until(app.root, lambda: not app.is_downloading)


def test_skip_failed_links_and_offer_retry(app):
    run_downloads(
        app,
        ["https://a", "https://b", "https://c"],
        {"https://b": "ERROR: Video unavailable"},
    )

    assert app.failed_links == ["https://b"]
    assert "1 file saved" not in app.status_label.cget("text")
    assert "2 files saved, 1 failed" in app.status_label.cget("text")
    assert "unavailable" in app.status_label.cget("text")
    assert app.retry_button.winfo_manager() == "pack"
    assert not app.is_downloading


def test_stop_everything_offers_the_unstarted_links_too(app):
    run_downloads(
        app,
        ["https://a", "https://b", "https://c"],
        {"https://a": "Read timed out."},
        stop=True,
    )

    assert app.failed_links == ["https://a", "https://b", "https://c"]
    assert "internet" in app.status_label.cget("text")
    assert app.retry_button.cget("text") == "Retry 3 failed"


def test_all_ok_shows_done_and_open_folder(app):
    run_downloads(app, ["https://a"], {})
    assert app.status_label.cget("text").startswith("✓  Done. 1 file saved")
    assert app.open_folder_button.winfo_manager() == "pack"
    assert app.retry_button.winfo_manager() == ""


def test_pause_resume_and_cancel(app):
    """Pause blocks the download thread; Resume lets it go on; Cancel while
    paused stops it cleanly."""

    progress = []

    def job(url, options):
        # A "download" that keeps going until it's cancelled (max ~30 s), so the
        # test controls the timing instead of racing a fast loop
        for _ in range(30_000):
            app.progress_hook(
                {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100}
            )
            progress.append(1)
            time.sleep(0.001)
        return 1

    app.is_downloading = True
    app.update_main_button()
    thread = threading.Thread(
        target=app.download_all, args=(["https://a"], job, {}, "file", False)
    )

    app.toggle_pause()  # paused before it even starts
    assert app.main_button.cget("text").endswith("Resume")
    thread.start()
    pump(app.root, 0.3)
    assert progress == []  # nothing happens while paused

    app.toggle_pause()  # resume
    assert wait_until(app.root, lambda: len(progress) > 0)

    app.toggle_pause()  # pause again, then cancel
    app.cancel_download()
    assert wait_until(app.root, lambda: not app.is_downloading)
    assert app.status_label.cget("text").startswith("Download cancelled")
    assert app.main_button.cget("text") == "Download"
    thread.join(2)
    assert not thread.is_alive()


# ---------- settings ----------


def test_settings_page_opens_and_closes(app):
    app.toggle_settings()
    assert app.settings_page.winfo_manager() == "pack"
    assert app.home_page.winfo_manager() == ""

    app.toggle_settings()
    assert app.home_page.winfo_manager() == "pack"
    assert app.settings_page.winfo_manager() == ""


def test_window_fits_a_small_laptop_screen(app):
    """Usability: the main screen (with progress shown) must fit 1366x768."""

    app.status_area.pack(fill="x", pady=(14, 0))
    app.show_status("Downloading 42%", main.MUTED)
    pump(app.root, 0.1)

    scale = app.root._get_window_scaling()
    height = app.root.winfo_reqheight() / scale  # in normal (100%) pixels
    assert height < 700, f"window needs {height:.0f}px"


def test_error_options_are_remembered(app):
    app.retry_menu.set("Retry 10 times")
    app.on_error_menu.set(main.STOP_ON_ERROR)
    app.save_settings()

    with open(main.SETTINGS_FILE, encoding="utf-8") as file:
        saved = json.load(file)

    assert saved["retry"] == "Retry 10 times"
    assert saved["on_error"] == main.STOP_ON_ERROR
    assert app.ydl_options()["retries"] == 10


def test_unexpected_ui_error_is_reported_not_silent(app):
    try:
        raise KeyError("oops")
    except KeyError as error:
        app.on_unexpected_error(KeyError, error, error.__traceback__)
    assert "Something went wrong" in app.status_label.cget("text")


# ---------- updates ----------


def test_update_banner_only_for_newer_versions(app):
    app.show_update_result({"version": "0.9.0", "size": 1}, None, manual=False)
    assert app.update_banner.winfo_manager() == ""

    app.show_update_result(
        {"version": "99.0.0", "size": 104_000_000}, None, manual=False
    )
    assert app.update_banner.winfo_manager() == "pack"
    assert "99.0.0 is available" in app.update_label.cget("text")


def test_update_waits_for_running_download(app):
    app.update_release = {"version": "99.0.0"}
    app.is_downloading = True
    app.start_update()
    assert "Finish or cancel" in app.update_label.cget("text")
