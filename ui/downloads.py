"""The Downloads page's pieces: one task per link, its row, and the history.

A DownloadTask is plain data. Its download runs on a background thread,
which only reads `cancel_requested` / `resume_event`; everything else is
changed on the UI thread (by the window).
"""

import itertools
import json
import os
import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    ProgressBar,
    SimpleCardWidget,
    ToolTipFilter,
    TransparentToolButton,
)

import core
from core import AUDIO, PHOTOS, human_size

from . import theme
from .widgets import Thumbnail, pil_to_pixmap

# What a download is doing
QUEUED = "queued"  # waiting for a free slot
DOWNLOADING = "downloading"
PAUSED = "paused"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE = (QUEUED, DOWNLOADING, PAUSED)

HISTORY_LIMIT = 100  # recent downloads remembered between sessions


class DownloadTask:
    """One link being downloaded (or finished)."""

    _ids = itertools.count(1)

    def __init__(self, url, mode, height=None, quality="", **details):
        self.id = next(self._ids)
        self.url = url
        self.mode = mode  # Video / Audio / Photos
        self.height = height  # exact pixel height to download (None = best)
        self.quality = quality  # what the user picked, e.g. "1080p"

        # What it is (from the preview, or from yt-dlp once it starts)
        self.title = details.get("title") or ""
        self.site = details.get("site") or ""
        self.thumbnail = details.get("thumbnail")  # PIL image, not saved
        self.thumbnail_url = details.get("thumbnail_url") or ""

        self.state = details.get("state", QUEUED)
        self.fraction = 0.0
        self.status = "Waiting to start…"  # line under the progress bar
        self.error = details.get("error") or ""
        self.saved = details.get("saved") or 0  # files saved
        self.path = details.get("path")  # the file (or photo folder) saved
        self.size = details.get("size") or 0
        self.finished_at = details.get("finished_at")

        # How to download it (set by the window when it's queued)
        self.job = None  # job(task) -> files saved
        self.options = None
        self.batch = 0  # links started together share a batch
        self.stop_on_error = False

        # Thread control
        self.running = False
        self.cancel_requested = False
        self.resume_event = threading.Event()
        self.resume_event.set()
        self.helper_process = None  # ffmpeg / gallery-dl, for pause / cancel
        self.asked_details = False

    @property
    def active(self):
        return self.state in ACTIVE

    def name(self):
        return self.title or self.url

    def meta(self):
        """ "YouTube · Video · 1080p" """

        parts = [self.site]
        if self.mode == AUDIO:
            parts += ["Audio", "MP3"]
        elif self.mode == PHOTOS:
            parts.append("Photos")
        else:
            parts += ["Video", self.quality]
        return "  ·  ".join(part for part in parts if part)

    def result_text(self):
        """The line shown once it's finished."""

        if self.state == DONE:
            parts = ["✓  Saved"]
            if self.mode == PHOTOS:
                parts.append(f"{self.saved} photo{'s' if self.saved != 1 else ''}")
            elif self.size:
                parts.append(human_size(self.size))
            parts.append(when(self.finished_at))
            return "  ·  ".join(part for part in parts if part)

        if self.state == CANCELLED:
            return "Cancelled  ·  " + when(self.finished_at)

        return f"✕  {self.error or 'Download failed.'}"

    # --- remembered between sessions ---

    def to_record(self):
        return {
            "url": self.url,
            "mode": self.mode,
            "height": self.height,
            "quality": self.quality,
            "title": self.title,
            "site": self.site,
            "thumbnail_url": self.thumbnail_url,
            "state": self.state,
            "error": self.error,
            "saved": self.saved,
            "path": self.path,
            "size": self.size,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_record(cls, record):
        details = {key: value for key, value in record.items() if key != "url"}
        mode = details.pop("mode", "Video")
        height = details.pop("height", None)
        quality = details.pop("quality", "")
        task = cls(record["url"], mode, height, quality, **details)
        if task.state in ACTIVE:
            # The app closed while it was downloading
            task.state, task.error = CANCELLED, ""
        return task


def when(timestamp):
    """ "5:30 PM" today, "Yesterday", or "12 Nov"."""

    if not timestamp:
        return ""

    then = time.localtime(timestamp)
    now = time.localtime()

    if then[:3] == now[:3]:
        return time.strftime("%I:%M %p", then).lstrip("0")

    yesterday = time.localtime(time.time() - 86400)
    if then[:3] == yesterday[:3]:
        return "Yesterday"

    return time.strftime("%d %b", then).lstrip("0")


def history_file():
    """Next to the settings file (so tests use their own temporary one)."""

    base, _ = os.path.splitext(core.SETTINGS_FILE)
    return base + "_downloads.json"


def load_history():
    try:
        with open(history_file(), encoding="utf-8") as file:
            records = json.load(file)
        return [DownloadTask.from_record(r) for r in records if r.get("url")]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def save_history(tasks):
    try:
        with open(history_file(), "w", encoding="utf-8") as file:
            json.dump([t.to_record() for t in tasks[:HISTORY_LIMIT]], file, indent=1)
    except OSError:
        pass  # a lost history isn't worth an error message


class ElidedLabel(BodyLabel):
    """One line that ends in "…" when it doesn't fit (never widens the window)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.elide()

    def elide(self):
        width = max(self.width(), 40)
        self.setText(
            self.fontMetrics().elidedText(
                self.full_text, Qt.TextElideMode.ElideRight, width
            )
        )


class DownloadRow(SimpleCardWidget):
    """A download in the list: picture, title, progress and its buttons."""

    def __init__(self, task, actions, parent=None):
        """actions: {"pause"|"cancel"|"open"|"folder"|"retry"|"remove": fn(task)}"""

        super().__init__(parent)
        self.task = task
        self.setBorderRadius(10)
        self.has_picture = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 8, 12)
        layout.setSpacing(14)

        self.thumbnail = Thumbnail(self, 112, 63)
        self.thumbnail.RADIUS = 6
        layout.addWidget(self.thumbnail, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(3)

        self.title = ElidedLabel(self)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        text.addWidget(self.title)

        self.meta = CaptionLabel(self)
        theme.paint(self.meta, theme.MUTED)
        text.addWidget(self.meta)

        text.addSpacing(4)
        self.progress = ProgressBar(self)
        self.progress.setRange(0, 1000)
        text.addWidget(self.progress)

        self.status = CaptionLabel(self)
        self.status.setWordWrap(True)
        text.addWidget(self.status)
        text.addStretch(1)

        layout.addLayout(text, 1)

        # Buttons; which ones show depends on the state (see refresh)
        self.buttons = {}
        for key, icon, tip in (
            ("pause", FluentIcon.PAUSE, "Pause"),
            ("open", FluentIcon.PLAY, "Open"),
            ("folder", FluentIcon.FOLDER, "Show in folder"),
            ("retry", FluentIcon.SYNC, "Try again"),
            ("cancel", FluentIcon.CLOSE, "Cancel"),
            ("remove", FluentIcon.DELETE, "Remove from list"),
        ):
            button = TransparentToolButton(icon, self)
            button.setToolTip(tip)
            button.installEventFilter(ToolTipFilter(button))
            button.clicked.connect(lambda _=False, k=key: actions[k](self.task))
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.buttons[key] = button

        self.refresh()

    def visible_buttons(self):
        return [key for key, button in self.buttons.items() if not button.isHidden()]

    def refresh(self):
        task = self.task

        self.title.set_full_text(task.name())
        self.meta.setText(task.meta())
        self.meta.setVisible(bool(task.meta()))

        if task.thumbnail is not None and not self.has_picture:
            self.thumbnail.set_image(pil_to_pixmap(task.thumbnail))
            self.has_picture = True
        elif not self.has_picture:
            self.thumbnail.set_placeholder(
                {AUDIO: FluentIcon.MUSIC, PHOTOS: FluentIcon.PHOTO}.get(
                    task.mode, FluentIcon.VIDEO
                )
            )

        if task.active:
            self.progress.show()
            self.progress.setValue(int(task.fraction * 1000))
            if task.state == PAUSED:
                self.progress.pause()
            else:
                self.progress.resume()
            self.status.setText(task.status)
            theme.paint(self.status, theme.MUTED)
        else:
            self.progress.hide()
            self.status.setText(task.result_text())
            theme.paint(self.status, theme.BAD if task.state == FAILED else theme.MUTED)

        pause = self.buttons["pause"]
        if task.state == PAUSED:
            pause.setIcon(FluentIcon.PLAY)
            pause.setToolTip("Resume")
        else:
            pause.setIcon(FluentIcon.PAUSE)
            pause.setToolTip("Pause")

        exists = bool(task.path) and os.path.exists(task.path)
        show = {
            "pause": task.running and not task.cancel_requested,
            "cancel": task.active and not task.cancel_requested,
            "open": task.state == DONE and exists,
            "folder": task.state == DONE,
            "retry": task.state in (FAILED, CANCELLED),
            "remove": not task.active,
        }
        for key, button in self.buttons.items():
            button.setVisible(show[key])
