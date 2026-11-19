"""Shared test setup.

- Tests never touch the real settings file or downloads folder (tmp_path).
- No test uses the internet, except ones marked `live` (skipped unless
  RUN_LIVE=1 is set).
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import customtkinter as ctk  # noqa: E402

import main  # noqa: E402


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return

    skip_live = pytest.mark.skip(reason="needs internet: set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def pump(root, seconds=0.2):
    """Let Tk process pending events/callbacks for a moment."""

    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def wait_until(root, condition, timeout=5.0):
    """Pump events until condition() is true (or fail after timeout)."""

    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="session")
def tk_root():
    """One hidden Tk window for the whole test run.

    Creating and destroying many Tk roots in one process sometimes fails on
    Windows ("Can't find a usable init.tcl"), so tests share one root and each
    test builds a fresh app inside it.
    """

    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(tk_root, tmp_path, monkeypatch):
    """A fresh app (hidden), with settings and downloads in tmp_path."""

    monkeypatch.setattr(main, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(main, "APP_DIR", str(tmp_path))

    application = main.VideoDownloader(tk_root)
    pump(tk_root, 0.1)

    yield application

    # Stop anything still running, then clear the window for the next test
    application.cancel_requested = True
    application.resume_event.set()

    for job in tk_root.tk.splitlist(tk_root.tk.call("after", "info")):
        tk_root.tk.call("after", "cancel", job)

    for child in tk_root.winfo_children():
        child.destroy()
