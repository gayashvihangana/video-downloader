"""Shared test setup.

- Tests never touch the real settings file or downloads folder (tmp_path).
- No test uses the internet, except ones marked `live` (skipped unless
  RUN_LIVE=1 is set).
- Windows are drawn off-screen, so nothing flashes up while tests run.
"""

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return

    skip_live = pytest.mark.skip(reason="needs internet: set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def pump(seconds=0.2):
    """Let Qt process pending events/callbacks for a moment."""

    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def wait_until(condition, timeout=5.0):
    """Pump events until condition() is true (or fail after timeout)."""

    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def shown(widget):
    """Visible as far as the app is concerned (the test window is hidden)."""

    return not widget.isHidden()


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole test run."""

    from ui import theme

    application = QApplication.instance() or QApplication([])
    theme.apply_theme("light")
    yield application


@pytest.fixture
def app(qapp, tmp_path, monkeypatch):
    """A fresh app window (not shown), with settings and downloads in tmp_path."""

    monkeypatch.setattr(core, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(core, "APP_DIR", str(tmp_path))

    from ui.window import VideoDownloader

    window = VideoDownloader()
    pump(0.05)

    yield window

    # Stop anything still running, then throw the window away
    window.cancel_all()
    wait_until(lambda: not any(task.running for task in window.tasks), timeout=3)
    window.deleteLater()
    pump(0.05)
