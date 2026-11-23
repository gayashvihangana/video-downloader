"""The real window (off-screen), driven like a user would, with downloads faked."""

import gc
import json
import threading
import time

import pytest
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


def test_chosen_quality_is_kept_by_name_across_video_shapes(app):
    """1080p picked on a wide video (really 804 pixels high) stays 1080p on a
    normal 1920x1080 video."""

    def qualities(height_1080, height_720):
        return [
            {"label": "1080p", "height": height_1080, "size": 0, "approx": True},
            {"label": "720p", "height": height_720, "size": 0, "approx": True},
        ]

    show(app, lookup_result("https://a", "Wide", qualities=qualities(804, 536)))
    app.quality.set("720p")
    app.on_quality_change("720p")
    assert app.qualities["720p"] == 536

    show(app, lookup_result("https://b", "Normal", qualities=qualities(1080, 720)))
    assert app.quality.get() == "720p"
    assert app.qualities[app.quality.get()] == 720  # downloads the real height


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


# ---------- downloading (jobs faked) ----------


def finished(app):
    return lambda: not app.is_downloading


def quick_job(outcomes=None):
    """A fake download: outcomes[link] = error message, or missing for ok."""

    outcomes = outcomes or {}

    def job(task):
        if outcomes.get(task.url):
            raise RuntimeError(outcomes[task.url])
        return 1

    return job


class SlowJob:
    """A fake download that reports progress until released or cancelled
    (max ~30 s), so a test controls the timing instead of racing it."""

    def __init__(self, app):
        self.app = app
        self.release = threading.Event()
        self.steps = []

    def __call__(self, task):
        for _ in range(30_000):
            if self.release.is_set():
                return 1
            self.app.progress_hook(
                task,
                {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 4},
            )
            self.steps.append(task.url)
            time.sleep(0.001)
        return 1


def test_download_button_queues_and_frees_home_for_the_next_link(app, monkeypatch):
    slow = SlowJob(app)
    monkeypatch.setattr(app, "download_video_link", slow)

    show(app, lookup_result("https://youtu.be/a", "First video"))
    app.url_box.setPlainText("https://youtu.be/a")
    app.start_download()

    # Home: "started", a way to the Downloads page, and an empty link box
    assert "Download started: First video" in app.status_label.text()
    assert shown(app.go_to_downloads_button)
    assert app.url_box.toPlainText() == ""

    # Downloads page: the link is a current download, with the preview's title
    [task] = app.tasks
    assert task.title == "First video"
    assert task.thumbnail is not None
    assert app.current_rows[task.id].title.full_text == "First video"
    assert "Current downloads (1)" in app.current_title.text()

    app.go_to_downloads_button.click()
    assert app.stackedWidget.currentWidget() is app.downloads_page

    slow.release.set()
    assert wait_until(finished(app))
    assert app.history[0].state == "done"
    assert app.history[0].url == "https://youtu.be/a"


def test_at_most_three_run_at_once_the_rest_wait(app):
    slow = SlowJob(app)
    links = [f"https://{name}" for name in "abcde"]
    app.queue_downloads(links, core.VIDEO, None, "Best", job=slow)

    assert [t.state for t in app.tasks].count("downloading") == 3
    assert [t.state for t in app.tasks].count("queued") == 2
    assert app.downloads_badge is not None and app.downloads_badge.text() == "5"

    slow.release.set()
    assert wait_until(finished(app))
    assert len(app.history) == 5
    assert app.downloads_badge.isHidden()
    assert app.current_title.text() == "Current downloads"


def test_failed_link_is_reported_and_can_be_retried(app):
    app.queue_downloads(
        ["https://a", "https://b", "https://c"],
        core.VIDEO,
        720,
        "720p",
        job=quick_job({"https://b": "ERROR: Video unavailable"}),
    )
    assert wait_until(finished(app))

    states = {t.url: t.state for t in app.history}
    assert states == {"https://a": "done", "https://b": "failed", "https://c": "done"}

    failed = next(t for t in app.history if t.state == "failed")
    assert "unavailable" in failed.error
    row = app.recent_rows[failed.id]
    assert "unavailable" in row.status.text()
    assert "retry" in row.visible_buttons()
    assert shown(app.retry_all_button)

    # Retry keeps the same choices (720p) and moves it back to Current
    slow = SlowJob(app)
    app.download_video_link = slow
    app.retry_task(failed)
    [again] = app.tasks
    assert (again.url, again.height, again.quality) == ("https://b", 720, "720p")
    assert failed not in app.history

    slow.release.set()
    assert wait_until(finished(app))
    assert not shown(app.retry_all_button)


def test_stop_after_an_error_skips_the_rest_of_those_links(app, monkeypatch):
    monkeypatch.setattr("ui.window.MAX_PARALLEL", 1)  # one at a time
    app.on_error_menu.set(core.STOP_ON_ERROR)

    app.queue_downloads(
        ["https://a", "https://b", "https://c"],
        core.VIDEO,
        None,
        "Best",
        job=quick_job({"https://a": "Read timed out."}),
    )
    assert wait_until(finished(app))

    errors = {t.url: t.error for t in app.history}
    assert "internet" in errors["https://a"]
    assert errors["https://b"] == "Not downloaded: stopped after an error."
    assert errors["https://c"] == "Not downloaded: stopped after an error."


def test_progress_from_the_download_thread_reaches_the_row(app):
    gate = threading.Event()

    def job(task):
        # Like yt-dlp: progress reported from the download thread
        app.progress_hook(
            task,
            {
                "status": "downloading",
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "speed": 1000,
                "eta": 65,
            },
        )
        gate.wait(5)
        return 1

    [task] = app.queue_downloads(["https://a"], core.VIDEO, None, "Best", job=job)

    assert wait_until(lambda: task.status.startswith("50%"))
    assert "50 B of 100 B" in task.status
    assert "1 KB/s" in task.status
    assert "1:05 left" in task.status
    row = app.current_rows[task.id]
    assert row.progress.value() == 500
    assert row.status.text() == task.status

    gate.set()
    assert wait_until(finished(app))


def test_pause_resume_and_cancel_one_download(app):
    """Pause blocks that download's thread; Resume lets it go on; Cancel while
    paused stops it cleanly and it shows as cancelled in Recent."""

    slow = SlowJob(app)
    [task] = app.queue_downloads(["https://a"], core.VIDEO, None, "Best", job=slow)
    assert wait_until(lambda: slow.steps)

    app.toggle_pause(task)
    assert task.state == "paused"
    row = app.current_rows[task.id]
    assert row.progress.isPaused()
    assert row.buttons["pause"].toolTip() == "Resume"
    pump(0.1)
    steps = len(slow.steps)
    pump(0.3)
    assert len(slow.steps) <= steps + 1  # nothing happens while paused

    app.toggle_pause(task)
    assert wait_until(lambda: len(slow.steps) > steps + 1)

    app.toggle_pause(task)  # pause again, then cancel
    app.cancel_task(task)
    assert wait_until(finished(app))
    assert app.history[0] is task
    assert task.state == "cancelled"
    assert "retry" in app.recent_rows[task.id].visible_buttons()


def test_cancel_a_download_that_is_still_waiting(app, monkeypatch):
    monkeypatch.setattr("ui.window.MAX_PARALLEL", 1)
    slow = SlowJob(app)
    first, second = app.queue_downloads(
        ["https://a", "https://b"], core.VIDEO, None, "Best", job=slow
    )
    assert second.state == "queued"

    app.cancel_task(second)
    assert second.state == "cancelled"
    assert app.tasks == [first]

    slow.release.set()
    assert wait_until(finished(app))


def test_recent_downloads_are_remembered_after_a_restart(app, tmp_path):
    app.queue_downloads(["https://a"], core.AUDIO, None, "Best", job=quick_job())
    assert wait_until(finished(app))
    app.history[0].title = "Song"
    from ui.downloads import save_history

    save_history(app.history)

    from ui.window import VideoDownloader

    again = VideoDownloader()
    try:
        [task] = again.history
        assert (task.url, task.title, task.state) == ("https://a", "Song", "done")
        assert again.recent_rows[task.id].meta.text() == "Audio  ·  MP3"
        assert again.recent_empty.isHidden()
    finally:
        again.deleteLater()
        pump(0.05)


def test_remove_and_clear_recent_downloads(app):
    app.queue_downloads(
        ["https://a", "https://b"], core.VIDEO, None, "Best", job=quick_job()
    )
    assert wait_until(finished(app))

    app.remove_task(app.history[0])
    assert len(app.history) == 1
    assert len(app.recent_rows) == 1

    app.clear_history()
    assert app.history == []
    assert shown(app.recent_empty)


def test_open_button_only_when_the_file_is_there(app, tmp_path):
    saved = tmp_path / "video.mp4"
    saved.write_bytes(b"x" * 10)

    def job(task):
        task.path = str(saved) if task.url == "https://a" else str(tmp_path / "gone")
        return 1

    app.queue_downloads(["https://a", "https://b"], core.VIDEO, None, "Best", job=job)
    assert wait_until(finished(app))

    tasks = {t.url: t for t in app.history}
    assert tasks["https://a"].size == 10
    assert "open" in app.recent_rows[tasks["https://a"].id].visible_buttons()
    assert "open" not in app.recent_rows[tasks["https://b"].id].visible_buttons()


# ---------- settings ----------


def test_sidebar_has_home_downloads_and_settings(app):
    names = [
        app.navigationInterface.widget(page.objectName()).text()
        for page in (app.home_page, app.downloads_page, app.settings_page)
    ]
    assert names == ["Home", "Downloads", "Settings"]

    app.show_downloads()
    assert app.stackedWidget.currentWidget() is app.downloads_page
    assert shown(app.current_empty)  # "Nothing is downloading"
    app.focus_links()
    assert app.stackedWidget.currentWidget() is app.home_page


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


def test_window_opens_without_ffmpeg(qapp, tmp_path, monkeypatch):
    """No ffmpeg on PATH (like a fresh PC or CI): the window still opens and
    Settings explains what's missing. It used to crash on start."""

    monkeypatch.setattr(core, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(core, "APP_DIR", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name, *args, **kwargs: None)

    from qfluentwidgets import SettingCard

    from ui.window import VideoDownloader

    window = VideoDownloader()
    try:
        assert not window.has_ffmpeg
        titles = [card.titleLabel.text() for card in window.findChildren(SettingCard)]
        assert "ffmpeg not found" in titles
    finally:
        window.deleteLater()
        pump(0.05)


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


def test_new_update_is_optional_at_first(app):
    app.show_update_result(
        {"version": "99.0.0", "size": 1_000_000, "published": time.time()},
        None,
        manual=False,
    )
    assert "within 7 days" in app.update_label.text()
    assert shown(app.later_button)
    assert not app.update_required
    assert app.main_button.text() == "Download"


def required_release():
    return {"version": "99.0.0", "size": 1_000_000, "required": True}


def test_required_update_blocks_new_downloads(app, monkeypatch):
    monkeypatch.setattr(core, "FROZEN", True)  # the installed app
    asked = []
    monkeypatch.setattr(app, "ask_to_update", lambda: asked.append(1))

    app.show_update_result(required_release(), None, manual=False)

    assert app.update_required
    assert asked == [1]  # "Update required: Update now / Quit"
    assert "99.0.0 is required" in app.update_label.text()
    assert not shown(app.later_button)  # can't be put off
    assert shown(app.website_button)
    assert app.main_button.text() == "Update to download"
    assert not app.main_button.isEnabled()

    # Ctrl+Enter asks again instead of downloading
    app.url_box.setPlainText("https://youtu.be/a")
    app.start_download()
    assert app.tasks == []
    assert asked == [1, 1]


def test_required_update_lets_running_downloads_finish(app, monkeypatch):
    monkeypatch.setattr(core, "FROZEN", True)
    asked = []
    monkeypatch.setattr(app, "ask_to_update", lambda: asked.append(1))

    slow = SlowJob(app)
    [task] = app.queue_downloads(["https://a"], core.VIDEO, None, "Best", job=slow)
    app.show_update_result(required_release(), None, manual=False)

    assert asked == []  # not while downloading
    assert task.state == "downloading"

    slow.release.set()
    assert wait_until(finished(app))
    assert wait_until(lambda: asked == [1])  # once it's done


def test_required_update_never_blocks_when_running_from_source(app, monkeypatch):
    monkeypatch.setattr(core, "FROZEN", False)
    monkeypatch.setattr(app, "ask_to_update", lambda: pytest.fail("asked"))

    app.show_update_result(required_release(), None, manual=False)

    assert not app.update_required
    assert "required for the installed app" in app.update_label.text()
    assert app.main_button.text() == "Download"


def test_failed_update_offers_try_again_and_the_website(app):
    app.update_required = True
    app.update_failed("Connection reset.")
    assert "Update failed: Connection reset. Try again" in app.update_label.text()
    assert app.update_button.text() == "Try again"
    assert shown(app.website_button)


def test_update_waits_for_running_download(app):
    slow = SlowJob(app)
    app.queue_downloads(["https://a"], core.VIDEO, None, "Best", job=slow)
    app.update_release = {"version": "99.0.0"}
    app.start_update()
    assert "Finish or cancel" in app.update_label.text()
    slow.release.set()
    assert wait_until(finished(app))
