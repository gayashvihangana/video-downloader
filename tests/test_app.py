"""The real window (off-screen), driven like a user would, with downloads faked."""

import gc
import json
import threading
import time

from conftest import pump, shown, wait_until
from PIL import Image

import core
from ui import theme


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
    pump(0.05)


# ---------- BUG-001 regression ----------


def test_bug001_second_link_after_a_thumbnail_shows_its_preview(app):
    """Download/cancel, then paste another link: preview got stuck on
    "Looking up…" because the old thumbnail was deleted but still in use."""

    show(app, lookup_result("https://a", "First video"))
    show(app, lookup_result("https://b", "Second video"))
    show(app, lookup_result("https://c", "Third video"))

    assert app.preview_title.text() == "Third video"
    assert app.thumbnail_image is not None
    assert not shown(app.loading_bar)


def test_error_after_a_thumbnail_does_not_get_stuck(app):
    show(app, lookup_result("https://a", "First video"))
    show(app, lookup_result("https://b", "", error="This video is private."))

    assert app.preview_title.text() == "Couldn't open this link"
    assert "private" in app.preview_message.text()
    assert app.thumbnail_image is None


def test_a_crash_while_drawing_the_preview_shows_a_message(app, monkeypatch):
    def broken(result):
        raise ValueError("boom")

    monkeypatch.setattr(app, "fill_preview", broken)
    app.lookup_request += 1
    app.show_lookup(lookup_result("https://a", "x"), app.lookup_request)

    assert app.preview_title.text() == "Couldn't show this link"


def test_old_lookup_answers_are_ignored(app):
    app.lookup_request = 5
    app.show_lookup(lookup_result("https://old", "Old video"), 4)
    assert app.preview_title.text() != "Old video"


def test_typing_a_link_starts_a_lookup_once_it_settles(app, monkeypatch):
    started = []
    monkeypatch.setattr(app, "start_lookup", lambda force=False: started.append(1))

    app.url_box.setPlainText("https://youtu.be/a")
    assert app.main_button.isEnabled()
    assert wait_until(lambda: started, timeout=2)


# ---------- choices offered ----------


def test_quality_menu_lists_sizes_and_remembers_height(app):
    show(app, lookup_result("https://a", "A"))
    assert app.quality.values() == ["720p  ·  9.0 MB", "360p  ·  ~3.0 MB"]

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
    assert app.mode.values() == ["Audio", "Photos"]
    assert app.preview_badges.text().count("photos") == 1


def test_audio_mode_shows_mp3_size_and_locks_quality(app):
    show(app, lookup_result("https://a", "A", duration=600))
    app.mode.set("Audio")  # like clicking it
    assert app.quality.get().startswith("MP3 · 192 kbps")
    assert not app.quality.isEnabled()


def test_download_button_disabled_without_links(app):
    app.clear_links()
    pump(0.1)
    assert not app.main_button.isEnabled()


def test_download_button_counts_links(app):
    app.url_box.setPlainText("https://a\nhttps://b")
    assert app.main_button.text() == "Download 2 links"


# ---------- checks before downloading ----------


def test_bad_lines_are_reported_and_nothing_starts(app):
    app.url_box.setPlainText("https://youtu.be/a\nnot a link")
    app.start_download()
    assert not app.is_downloading
    assert "Line 2 isn't a link" in app.status_label.text()


def test_mp3_without_ffmpeg_is_explained(app):
    app.has_ffmpeg = False
    app.set_modes(["Video", "Audio"])
    app.mode.set("Audio")
    app.url_box.setPlainText("https://youtu.be/a")
    app.start_download()
    assert not app.is_downloading
    assert "ffmpeg" in app.status_label.text()


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
    assert wait_until(lambda: not app.is_downloading)


def test_skip_failed_links_and_offer_retry(app):
    run_downloads(
        app,
        ["https://a", "https://b", "https://c"],
        {"https://b": "ERROR: Video unavailable"},
    )

    assert app.failed_links == ["https://b"]
    assert "1 file saved" not in app.status_label.text()
    assert "2 files saved, 1 failed" in app.status_label.text()
    assert "unavailable" in app.status_label.text()
    assert shown(app.retry_button)
    assert not app.is_downloading


def test_stop_everything_offers_the_unstarted_links_too(app):
    run_downloads(
        app,
        ["https://a", "https://b", "https://c"],
        {"https://a": "Read timed out."},
        stop=True,
    )

    assert app.failed_links == ["https://a", "https://b", "https://c"]
    assert "internet" in app.status_label.text()
    assert app.retry_button.text() == "Retry 3 failed"


def test_all_ok_shows_done_and_open_folder(app):
    run_downloads(app, ["https://a"], {})
    assert app.status_label.text().startswith("✓  Done. 1 file saved")
    assert shown(app.open_folder_button)
    assert not shown(app.retry_button)


def test_progress_from_the_download_thread_reaches_the_window(app):
    app.is_downloading = True
    thread = threading.Thread(
        target=app.progress_hook,
        args=(
            {
                "status": "downloading",
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "speed": 1000,
            },
        ),
    )
    thread.start()
    thread.join()

    assert wait_until(lambda: app.status_label.text() == "Downloading 50%")
    assert "/s" in app.speed_label.text()
    assert app.progress.value() == 500


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
    assert app.main_button.text() == "Resume"
    assert app.progress.isPaused()
    thread.start()
    pump(0.3)
    assert progress == []  # nothing happens while paused

    app.toggle_pause()  # resume
    assert wait_until(lambda: len(progress) > 0)

    app.toggle_pause()  # pause again, then cancel
    app.cancel_download()
    assert wait_until(lambda: not app.is_downloading)
    assert app.status_label.text().startswith("Download cancelled")
    assert app.main_button.text() == "Download"
    assert not shown(app.cancel_button)
    thread.join(2)
    assert not thread.is_alive()


# ---------- settings ----------


def test_settings_page_opens_and_closes(app):
    app.toggle_settings()
    assert app.stackedWidget.currentWidget() is app.settings_page

    app.toggle_settings()
    assert app.stackedWidget.currentWidget() is app.home_page


def test_window_fits_a_small_laptop_screen(app):
    """Usability: the window must fit 1366x768, and its pages scroll rather
    than forcing the window bigger."""

    app.show_status("Downloading 42%", theme.MUTED)
    app.show_update_banner("Version 9 is available", can_update=True)
    pump(0.1)

    assert app.width() <= 1366 and app.height() <= 728
    assert app.minimumSizeHint().height() <= 728


def test_bug005_long_messages_do_not_widen_the_window(app):
    """Long messages wrap instead of making the window wider."""

    show(app, {**lookup_result("https://a", ""), "error": "word " * 80})
    app.set_sign_in_status("✕  " + "word " * 60, theme.BAD)
    pump(0.1)

    assert app.minimumSizeHint().width() <= 1000


def test_error_options_are_remembered(app):
    app.retry_menu.set("Retry 10 times")
    app.on_error_menu.set(core.STOP_ON_ERROR)
    app.save_settings()

    with open(core.SETTINGS_FILE, encoding="utf-8") as file:
        saved = json.load(file)

    assert saved["retry"] == "Retry 10 times"
    assert saved["on_error"] == core.STOP_ON_ERROR
    assert app.ydl_options()["retries"] == 10


def test_theme_choice_is_remembered(app):
    app.set_theme("dark")
    try:
        with open(core.SETTINGS_FILE, encoding="utf-8") as file:
            assert json.load(file)["theme"] == "dark"
        assert app.theme_menu.get() == "Dark"

        app.toggle_theme()
        assert app.settings["theme"] == "light"
    finally:
        theme.apply_theme("light")


def test_unexpected_ui_error_is_reported_not_silent(app):
    try:
        raise KeyError("oops")
    except KeyError as error:
        app.on_unexpected_error(KeyError, error, error.__traceback__)
    assert "Something went wrong" in app.status_label.text()


# ---------- updates ----------


def test_update_banner_only_for_newer_versions(app):
    app.show_update_result({"version": "0.9.0", "size": 1}, None, manual=False)
    assert not shown(app.update_banner)

    app.show_update_result(
        {"version": "99.0.0", "size": 104_000_000}, None, manual=False
    )
    assert shown(app.update_banner)
    assert shown(app.update_button)
    assert "99.0.0 is available" in app.update_label.text()


def test_update_waits_for_running_download(app):
    app.update_release = {"version": "99.0.0"}
    app.is_downloading = True
    app.start_update()
    assert "Finish or cancel" in app.update_label.text()
