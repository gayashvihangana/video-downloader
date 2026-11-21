"""The main window: a Fluent (Windows 11 style) app with a sidebar.

Pages: Download (home) and Settings. Background threads never touch widgets;
they hand work to the UI thread with self.ui(...).
"""

import json
import os
import re
import shutil
import subprocess
import threading

import yt_dlp
from PySide6.QtCore import QObject, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    FluentWindow,
    HorizontalSeparator,
    HyperlinkButton,
    IconWidget,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    NavigationItemPosition,
    PlainTextEdit,
    PrimaryPushButton,
    PrimaryPushSettingCard,
    ProgressBar,
    PushButton,
    PushSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SimpleCardWidget,
    StrongBodyLabel,
    TitleLabel,
    ToolTipFilter,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
)
from yt_dlp.utils import DownloadCancelled

import core
from core import (
    APP_VERSION,
    AUDIO,
    BEST_QUALITY,
    BROWSERS,
    COOKIES_FILE,
    DEFAULT_RETRY,
    FROZEN,
    MP3_KBPS,
    NO_SIGN_IN,
    NO_WATERMARK,
    NO_WINDOW,
    PHOTOS,
    PLAYS_EVERYWHERE,
    RETRY_CHOICES,
    SITE_LOGIN_COOKIES,
    SKIP_FAILED,
    SMALLER_FILES,
    STOP_ON_ERROR,
    VIDEO,
    CleanTitle,
    MakePlayable,
    QuietLogger,
    human_size,
    human_time,
    log,
)

from . import theme
from .theme import BAD, GOOD, MUTED, WARN, paint
from .widgets import (
    Banner,
    Choice,
    ModeSelector,
    SignInPanel,
    TagRow,
    Thumbnail,
    pil_to_pixmap,
)

CONTENT_WIDTH = 860  # pages stay readable on wide screens


class Dispatcher(QObject):
    """Carries calls from any thread to the UI thread (queued signal)."""

    call = Signal(object)


def page(name):
    """A scrolling page whose content is centred and width-limited.
    Returns (scroll_area, layout to fill)."""

    area = ScrollArea()
    area.setObjectName(name)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.enableTransparentBackground()

    holder = QWidget()
    holder.setObjectName(f"{name}Holder")
    holder.setStyleSheet(f"#{name}Holder{{background:transparent}}")
    outer = QHBoxLayout(holder)
    outer.setContentsMargins(36, 20, 36, 28)

    body = QWidget(holder)
    body.setMaximumWidth(CONTENT_WIDTH)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    outer.addWidget(body)

    area.setWidget(holder)
    return area, layout


def card(layout_class=QVBoxLayout, margins=(20, 18, 20, 18), spacing=10):
    widget = SimpleCardWidget()
    widget.setBorderRadius(10)
    inner = layout_class(widget)
    inner.setContentsMargins(*margins)
    inner.setSpacing(spacing)
    return widget, inner


def muted(label):
    paint(label, MUTED)
    return label


class VideoDownloader(FluentWindow):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.settings = self.load_settings()

        # Installed app: save to Downloads\Video Downloader (the install folder
        # isn't a good place for files). From source: the project's downloads.
        if FROZEN:
            default_folder = os.path.join(
                os.path.expanduser("~"), "Downloads", "Video Downloader"
            )
        else:
            default_folder = os.path.join(core.APP_DIR, "downloads")

        folder = self.settings.get("folder") or ""
        self.download_folder = folder if os.path.isdir(folder) else default_folder
        os.makedirs(self.download_folder, exist_ok=True)

        # ffmpeg is needed to merge video+audio streams and to convert to MP3
        self.has_ffmpeg = shutil.which("ffmpeg") is not None

        # State of the current link lookup
        self.lookup = None  # result dict for the first link
        self.lookup_url = None
        self.lookup_request = 0  # increases per lookup, so stale results are ignored
        self.qualities = {}  # dropdown text -> pixel height (None = best)
        self.quality_list = []
        self.photo_cache = {}  # url -> (photos, post details)
        self.thumbnail_image = None
        self.preferred_height = None  # height the user picked; kept per link

        # Sign-in
        self.cookie_file = self.settings.get("cookie_file")
        self.sign_in_method = NO_SIGN_IN  # a browser name, COOKIES_FILE or none
        self.sign_in_request = 0
        self.sign_in_site = None

        # Download state
        self.is_downloading = False
        self.cancel_requested = False
        self.helper_process = None
        self.current_item = (0, 0)  # (index, total)
        self.last_fraction = 0

        # Pause: the download thread waits while this event is cleared
        self.paused = False
        self.resume_event = threading.Event()
        self.resume_event.set()

        # Links that failed in the last run (for "Retry failed")
        self.failed_links = []
        self.update_release = None

        # Work handed from background threads to the UI thread (see ui())
        self.dispatcher = Dispatcher(self)
        self.dispatcher.call.connect(
            self.run_ui_call, Qt.ConnectionType.QueuedConnection
        )

        # Look links up once typing/pasting settles
        self.url_timer = QTimer(self)
        self.url_timer.setSingleShot(True)
        self.url_timer.setInterval(600)
        self.url_timer.timeout.connect(self.on_urls_settled)

        self.create_interface()
        self.restore_settings()

        # The installed app checks for updates quietly a moment after it opens
        if FROZEN:
            QTimer.singleShot(2500, self, self.check_for_updates)

    # -------------------------------
    # SETTINGS (remembered between sessions)
    # -------------------------------

    @staticmethod
    def load_settings():

        try:
            with open(core.SETTINGS_FILE, encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError):
            return {}

    def theme_name(self):

        value = self.settings.get("theme", "system")
        return value if value in theme.THEMES else "system"

    def save_settings(self):

        self.settings.update(
            {
                "folder": self.download_folder,
                "sign_in": self.sign_in_method,
                "cookie_file": self.cookie_file,
                "theme": self.theme_name(),
                "retry": self.retry_menu.get(),
                "video_format": self.video_format_menu.get(),
                "on_error": self.on_error_menu.get(),
            }
        )

        try:
            with open(core.SETTINGS_FILE, "w", encoding="utf-8") as file:
                json.dump(self.settings, file, indent=2)
        except OSError:
            pass  # not being able to remember settings isn't worth an error

    def restore_settings(self):

        sign_in = self.settings.get("sign_in", NO_SIGN_IN)

        if sign_in == COOKIES_FILE and not (
            self.cookie_file and os.path.isfile(self.cookie_file)
        ):
            sign_in = NO_SIGN_IN

        if sign_in in BROWSERS:
            self.sign_in_method = sign_in
            self.update_sign_in_chip()

    # -------------------------------
    # INTERFACE
    # -------------------------------

    def create_interface(self):

        self.setWindowTitle("Video Downloader")
        if os.path.isfile(core.ICON_FILE):
            self.setWindowIcon(QIcon(core.ICON_FILE))

        self.home_page = self.create_home_page()
        self.settings_page = self.create_settings_page()

        self.addSubInterface(self.home_page, FluentIcon.DOWNLOAD, "Download")
        self.navigationInterface.addItem(
            routeKey="theme",
            icon=FluentIcon.CONSTRACT,
            text="Switch theme",
            onClick=self.toggle_theme,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self.settings_page,
            FluentIcon.SETTING,
            "Settings",
            NavigationItemPosition.BOTTOM,
        )
        self.stackedWidget.currentChanged.connect(self.on_page_changed)

        # Fits a 1366x768 laptop; content scrolls if the window is smaller
        self.resize(980, 720)
        self.setMinimumSize(720, 520)
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(
                area.center().x() - self.width() // 2,
                area.center().y() - self.height() // 2,
            )

        # Keyboard shortcuts
        shortcuts = {
            "Ctrl+Return": self.on_main_button,
            "Escape": self.cancel_download,
            "Ctrl+P": self.toggle_pause,
            "Ctrl+L": lambda: self.url_box.setFocus(),
        }
        for keys, action in shortcuts.items():
            QShortcut(QKeySequence(keys), self, action)

        self.update_main_button()

    def create_home_page(self):

        area, layout = page("home")

        title = TitleLabel("Download", area)
        subtitle = muted(
            CaptionLabel(
                "Videos, music and photos from YouTube, TikTok, Instagram and "
                "Facebook.",
                area,
            )
        )
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        self.create_update_banner(layout)
        self.create_links_card(layout)
        self.create_preview_card(layout)
        self.create_action_area(layout)
        layout.addStretch(1)

        return area

    def create_update_banner(self, layout):

        # Hidden until an update is found (or the user checks by hand)
        self.update_banner = Banner(FluentIcon.UPDATE)
        self.update_label = self.update_banner.label

        self.update_button = self.update_banner.add_button(
            PrimaryPushButton("Update now")
        )
        self.update_button.clicked.connect(self.start_update)
        self.later_button = self.update_banner.add_button(PushButton("Later"))
        self.later_button.clicked.connect(self.hide_update_banner)

        self.update_banner.hide()
        layout.addWidget(self.update_banner)

    def create_links_card(self, layout):

        widget, inner = card()

        self.url_box = PlainTextEdit(widget)
        self.url_box.setPlaceholderText("Paste a link here  (one per line for several)")
        self.url_box.setFixedHeight(92)
        self.url_box.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.url_box.textChanged.connect(self.on_urls_changed)
        inner.addWidget(self.url_box)

        row = QHBoxLayout()
        row.setSpacing(8)

        paste = PushButton(FluentIcon.PASTE, "Paste", widget)
        paste.clicked.connect(self.paste_link)
        clear = PushButton(FluentIcon.DELETE, "Clear", widget)
        clear.clicked.connect(self.clear_links)
        row.addWidget(paste)
        row.addWidget(clear)
        row.addStretch(1)
        row.addWidget(muted(CaptionLabel("Ctrl+Enter to download", widget)))

        inner.addLayout(row)
        layout.addWidget(widget)

    def create_preview_card(self, layout):

        widget, inner = card(spacing=14)

        top = QHBoxLayout()
        top.setSpacing(16)

        self.thumbnail = Thumbnail(widget)
        top.addWidget(self.thumbnail, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(4)

        self.preview_title = StrongBodyLabel("No link yet", widget)
        self.preview_title.setWordWrap(True)
        font = self.preview_title.font()
        font.setPixelSize(16)
        self.preview_title.setFont(font)

        self.preview_meta = muted(
            CaptionLabel(
                "Paste a link above. Details, sizes and qualities appear here.",
                widget,
            )
        )
        self.preview_meta.setWordWrap(True)

        self.preview_badges = TagRow(widget)
        self.preview_badges.hide()

        self.preview_message = BodyLabel(widget)
        self.preview_message.setWordWrap(True)
        self.preview_message.hide()

        text.addWidget(self.preview_title)
        text.addWidget(self.preview_meta)
        text.addSpacing(4)
        text.addWidget(self.preview_badges)
        text.addWidget(self.preview_message)
        text.addStretch(1)
        top.addLayout(text, 1)

        self.refresh_button = TransparentToolButton(FluentIcon.SYNC, widget)
        self.refresh_button.setToolTip("Look up the link again")
        self.refresh_button.installEventFilter(ToolTipFilter(self.refresh_button))
        self.refresh_button.clicked.connect(lambda: self.start_lookup(force=True))
        self.refresh_button.hide()
        top.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignTop)

        inner.addLayout(top)

        self.loading_bar = IndeterminateProgressBar(widget, start=False)
        self.loading_bar.hide()
        inner.addWidget(self.loading_bar)

        self.sign_in_panel = SignInPanel(widget)
        self.sign_in_panel.hide()
        self.sign_in_text = self.sign_in_panel.text
        self.sign_in_status = self.sign_in_panel.status
        inner.addWidget(self.sign_in_panel)

        inner.addWidget(HorizontalSeparator(widget))

        # Download as / Quality
        options = QGridLayout()
        options.setHorizontalSpacing(16)
        options.setVerticalSpacing(12)
        options.setColumnStretch(2, 1)

        options.addWidget(BodyLabel("Download as", widget), 0, 0)
        self.mode = ModeSelector(widget)
        self.mode.set_values([VIDEO, AUDIO])
        self.mode.set(VIDEO)
        self.mode.currentItemChanged.connect(self.on_mode_change)
        options.addWidget(self.mode, 0, 1)

        options.addWidget(BodyLabel("Quality", widget), 1, 0)
        self.quality = Choice(widget)
        self.quality.setMinimumWidth(300)
        self.quality.set_values([BEST_QUALITY], BEST_QUALITY)
        self.quality.textActivated.connect(self.on_quality_change)
        options.addWidget(self.quality, 1, 1)

        inner.addLayout(options)
        layout.addWidget(widget)

    def create_action_area(self, layout):

        actions = QHBoxLayout()
        actions.setSpacing(10)

        # Download  ->  while downloading: Pause / Resume
        self.main_button = PrimaryPushButton(FluentIcon.DOWNLOAD, "Download")
        self.main_button.setFixedHeight(44)
        font = self.main_button.font()
        font.setPixelSize(15)
        font.setBold(True)
        self.main_button.setFont(font)
        self.main_button.setIconSize(QSize(18, 18))
        self.main_button.clicked.connect(self.on_main_button)
        actions.addWidget(self.main_button, 1)

        # Only shown while downloading; smaller, so it's harder to hit by mistake
        self.cancel_button = PushButton(FluentIcon.CLOSE, "Cancel")
        self.cancel_button.setFixedSize(120, 44)
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.hide()
        actions.addWidget(self.cancel_button)

        layout.addSpacing(2)
        layout.addLayout(actions)

        # Progress / result (hidden until a download starts)
        self.status_area, status = card(spacing=10, margins=(20, 16, 20, 16))

        top = QHBoxLayout()
        self.status_label = BodyLabel(self.status_area)
        self.status_label.setWordWrap(True)
        top.addWidget(self.status_label, 1)
        self.speed_label = muted(CaptionLabel(self.status_area))
        top.addWidget(self.speed_label, 0, Qt.AlignmentFlag.AlignTop)
        status.addLayout(top)

        self.progress = ProgressBar(self.status_area)
        self.progress.setRange(0, 1000)
        status.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        # Shown after a run where some links failed
        self.retry_button = PushButton(FluentIcon.SYNC, "Retry failed")
        self.retry_button.clicked.connect(self.retry_failed)
        self.retry_button.hide()
        self.open_folder_button = PushButton(FluentIcon.FOLDER, "Open folder")
        self.open_folder_button.clicked.connect(self.open_folder)
        self.open_folder_button.hide()
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.open_folder_button)
        status.addLayout(buttons)

        self.status_area.hide()
        layout.addWidget(self.status_area)

        # Where files go (matters for every download, so always visible) and
        # who you're signed in as
        footer = QHBoxLayout()
        footer.setSpacing(6)

        folder_icon = IconWidget(FluentIcon.FOLDER)
        folder_icon.setFixedSize(14, 14)
        footer.addWidget(folder_icon)
        self.footer_folder = muted(CaptionLabel())
        footer.addWidget(self.footer_folder)

        change = HyperlinkButton()
        change.setText("Change")
        change.clicked.connect(self.choose_folder)
        footer.addWidget(change)
        footer.addStretch(1)

        # "Signed in · Firefox": click to sign out
        self.sign_in_chip = TransparentPushButton(FluentIcon.PEOPLE, "")
        self.sign_in_chip.setToolTip("Click to sign out")
        self.sign_in_chip.installEventFilter(ToolTipFilter(self.sign_in_chip))
        self.sign_in_chip.clicked.connect(self.sign_out)
        self.sign_in_chip.hide()
        footer.addWidget(self.sign_in_chip)

        layout.addLayout(footer)
        self.show_folder()

    def create_settings_page(self):
        """Less-used options live on their own page, so the main screen
        stays short and simple."""

        area, layout = page("settings")
        layout.setSpacing(20)
        layout.addWidget(TitleLabel("Settings", area))

        def choice_card(group, icon, title, content, values, current, on_change):
            setting = SettingCard(icon, title, content, group)
            menu = Choice(setting)
            menu.setMinimumWidth(220)
            menu.set_values(values, current if current in values else values[0])
            menu.currentTextChanged.connect(on_change)
            setting.hBoxLayout.addWidget(menu, 0, Qt.AlignmentFlag.AlignRight)
            setting.hBoxLayout.addSpacing(16)
            group.addSettingCard(setting)
            return menu

        # --- Downloads ---
        downloads = SettingCardGroup("Downloads", area)

        self.folder_card = PushSettingCard(
            "Change",
            FluentIcon.FOLDER,
            "Save downloads to",
            self.download_folder,
            downloads,
        )
        self.folder_card.clicked.connect(self.choose_folder)
        downloads.addSettingCard(self.folder_card)

        self.video_format_menu = choice_card(
            downloads,
            FluentIcon.VIDEO,
            "Video format",
            "Plays everywhere works on every PC, phone and TV. Smaller files "
            "may play with sound only on some PCs.",
            [PLAYS_EVERYWHERE, SMALLER_FILES],
            self.settings.get("video_format", PLAYS_EVERYWHERE),
            self.on_video_format_change,
        )

        # Sign-in isn't here on purpose: it's offered right where it's needed
        # (on a link that needs an account), and signing out is on the chip.
        if not self.has_ffmpeg:
            warning = SettingCard(
                FluentIcon.INFO,
                "ffmpeg not found",
                "MP3 is unavailable and video quality is limited. Install it "
                "with: winget install Gyan.FFmpeg",
                downloads,
            )
            paint(warning.contentLabel, WARN)
            downloads.addSettingCard(warning)

        layout.addWidget(downloads)

        # --- When something goes wrong ---
        errors = SettingCardGroup("When something goes wrong", area)

        self.retry_menu = choice_card(
            errors,
            FluentIcon.SYNC,
            "If a download fails",
            "Retries help with slow or unstable internet.",
            list(RETRY_CHOICES),
            self.settings.get("retry", DEFAULT_RETRY),
            lambda value: self.save_settings(),
        )
        if self.retry_menu.get() != self.settings.get("retry", DEFAULT_RETRY):
            self.retry_menu.set(DEFAULT_RETRY)

        self.on_error_menu = choice_card(
            errors,
            FluentIcon.CANCEL,
            "With several links",
            "When one link fails: skip it and download the rest, or stop.",
            [SKIP_FAILED, STOP_ON_ERROR],
            self.settings.get("on_error", SKIP_FAILED),
            lambda value: self.save_settings(),
        )

        log_card = PushSettingCard(
            "Open",
            FluentIcon.DOCUMENT,
            "Error log",
            "Details about anything that went wrong, for fixing bugs.",
            errors,
        )
        log_card.clicked.connect(self.open_log)
        errors.addSettingCard(log_card)
        layout.addWidget(errors)

        # --- Personalization ---
        looks = SettingCardGroup("Personalization", area)
        names = theme.THEME_NAMES
        self.theme_menu = choice_card(
            looks,
            FluentIcon.BRUSH,
            "App theme",
            "Light, dark, or follow Windows.",
            list(names.values()),
            names[self.theme_name()],
            self.on_theme_choice,
        )
        layout.addWidget(looks)

        # --- About ---
        about = SettingCardGroup("About", area)
        update_card = PrimaryPushSettingCard(
            "Check for updates",
            FluentIcon.INFO,
            "Video Downloader",
            f"Version {APP_VERSION}",
            about,
        )
        update_card.clicked.connect(lambda: self.check_for_updates(manual=True))
        about.addSettingCard(update_card)
        layout.addWidget(about)

        layout.addStretch(1)
        return area

    # -------------------------------
    # PAGES & THEME
    # -------------------------------

    def show_settings(self):

        self.switchTo(self.settings_page)

    def show_home(self):

        self.switchTo(self.home_page)

    def toggle_settings(self):

        if self.stackedWidget.currentWidget() is self.settings_page:
            self.show_home()
        else:
            self.show_settings()

    def on_page_changed(self, index):

        self.save_settings()

    def set_theme(self, name):

        self.settings["theme"] = name
        theme.apply_theme(name)

        with QSignalBlocker(self.theme_menu):
            self.theme_menu.set(theme.THEME_NAMES[name])

        self.save_settings()

    def toggle_theme(self):

        self.set_theme("light" if isDarkTheme() else "dark")

    def on_theme_choice(self, text):

        name = next(
            (key for key, value in theme.THEME_NAMES.items() if value == text),
            "system",
        )
        self.set_theme(name)

    # -------------------------------
    # SMALL UI HELPERS
    # -------------------------------

    def plays_everywhere(self):

        return self.video_format_menu.get() == PLAYS_EVERYWHERE

    def on_video_format_change(self, value):

        self.update_quality_menu()  # sizes and "converts" labels change
        self.save_settings()

    def retries(self):

        return RETRY_CHOICES.get(self.retry_menu.get(), 3)

    def open_log(self):

        if os.path.isfile(core.LOG_FILE):
            os.startfile(core.LOG_FILE)
        else:
            self.toast("success", "All good", "Nothing has gone wrong yet.")

    @staticmethod
    def short_path(path, max_length=56):

        path = os.path.normpath(path)

        if len(path) <= max_length:
            return path

        parts = path.split(os.sep)
        short = os.path.join("…", *parts[-2:])

        return short if len(short) <= max_length else "…" + path[-(max_length - 1) :]

    def show_folder(self):

        self.footer_folder.setText(
            f"Saving to  {self.short_path(self.download_folder)}"
        )
        self.footer_folder.setToolTip(self.download_folder)
        if hasattr(self, "folder_card"):
            self.folder_card.setContent(self.download_folder)

    def toast(self, kind, title, content, duration=4000):
        """A Fluent notification in the top-right corner of the window."""

        show = {
            "success": InfoBar.success,
            "error": InfoBar.error,
            "warning": InfoBar.warning,
            "info": InfoBar.info,
        }[kind]
        show(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=duration,
            parent=self,
        )

    def get_urls(self):

        text = self.url_box.toPlainText()

        return [url.strip() for url in text.splitlines() if url.strip()]

    def paste_link(self):

        text = QApplication.clipboard().text().strip()

        if not text:
            return

        current = self.url_box.toPlainText().strip()
        cursor = self.url_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(("\n" if current else "") + text)
        self.url_box.setTextCursor(cursor)
        self.url_box.setFocus()

    def clear_links(self):

        self.url_box.clear()
        self.url_box.setFocus()

    def choose_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self, "Choose where to save downloads", self.download_folder
        )

        if folder:
            self.download_folder = os.path.normpath(folder)
            self.show_folder()
            self.save_settings()

    def open_folder(self):

        if os.path.isdir(self.download_folder):
            os.startfile(self.download_folder)

    def update_main_button(self):

        if self.is_downloading:
            if self.paused:
                self.main_button.setText("Resume")
                self.main_button.setIcon(FluentIcon.PLAY)
            else:
                self.main_button.setText("Pause")
                self.main_button.setIcon(FluentIcon.PAUSE)

            self.main_button.setEnabled(True)
            self.cancel_button.setEnabled(not self.cancel_requested)
            self.cancel_button.show()
            return

        self.cancel_button.hide()

        urls = self.get_urls()
        count = len(urls)

        if self.mode.get() == PHOTOS:
            text = "Download photos"
        elif count > 1:
            text = f"Download {count} links"
        else:
            text = "Download"

        self.main_button.setText(text)
        self.main_button.setIcon(FluentIcon.DOWNLOAD)
        self.main_button.setEnabled(bool(urls))

    # -------------------------------
    # LINK LOOKUP (details, qualities, photos, thumbnail)
    # -------------------------------

    def on_urls_changed(self):

        self.update_main_button()

        # Wait until typing/pasting settles before looking anything up
        self.url_timer.start()

    def on_urls_settled(self):

        urls = self.get_urls()
        first = urls[0] if urls else None

        if first == self.lookup_url:
            return

        if first and re.match(r"(https?://|www\.)", first):
            self.start_lookup()
        else:
            self.lookup_url = None
            self.lookup_request += 1
            self.show_empty_preview()

    def ydl_options(self):

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "logger": QuietLogger(),
            # YouTube hides its video streams behind a JavaScript challenge.
            # yt-dlp solves it with a JS runtime: Deno if present, else Node.js.
            "js_runtimes": {"deno": {}, "node": {}},
            **core.retry_options(self.retries()),
        }

        sign_in = self.sign_in_method

        if sign_in == COOKIES_FILE and self.cookie_file:
            options["cookiefile"] = self.cookie_file
        elif sign_in not in (NO_SIGN_IN, COOKIES_FILE):
            options["cookiesfrombrowser"] = (sign_in.lower(),)

        return options

    def gallery_cookie_args(self):

        sign_in = self.sign_in_method

        if sign_in == COOKIES_FILE and self.cookie_file:
            return ["--cookies", self.cookie_file]

        if sign_in not in (NO_SIGN_IN, COOKIES_FILE):
            return ["--cookies-from-browser", sign_in.lower()]

        return []

    def start_lookup(self, force=False):

        urls = self.get_urls()

        if not urls:
            return

        url = urls[0]

        if force:
            self.photo_cache.pop(url, None)

        self.lookup_url = url
        self.lookup = None
        self.lookup_request += 1

        self.show_loading_preview()

        # Read widget values here: widgets must only be touched on the UI thread
        threading.Thread(
            target=self.run_lookup,
            args=(
                url,
                self.ydl_options(),
                self.gallery_cookie_args(),
                self.lookup_request,
            ),
            daemon=True,
        ).start()

    def run_lookup(self, url, ydl_options, cookie_args, request):

        result = {
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
            "error": None,
            "needs_sign_in": False,
        }

        try:
            self.lookup_link(url, ydl_options, cookie_args, result)
        except Exception:
            # A bug in our own code: never leave the preview stuck on "Looking up"
            log.exception("Lookup crashed for %s", url)
            result["error"] = (
                "Something went wrong while reading this link. Try again with ↻. "
                "(Details were saved to the error log.)"
            )

        self.ui(self.show_lookup, result, request)

    def lookup_link(self, url, ydl_options, cookie_args, result):

        # 1. Video / audio via yt-dlp
        try:
            with yt_dlp.YoutubeDL(ydl_options) as downloader:
                info = downloader.extract_info(url, download=False)

            result.update(core.summarize_video(info, not self.has_ffmpeg))

        except Exception as error:
            log.warning("yt-dlp lookup failed for %s: %s", url, error)
            result["error"] = core.friendly_error(error)
            result["needs_sign_in"] = core.needs_sign_in(error)

        # 2. Photos via gallery-dl (TikTok slideshows, Instagram, Facebook)
        if core.is_photo_host(url):
            try:
                photos, meta = core.find_photos(url, cookie_args)
                details = core.post_details(meta) if meta else {}
                self.photo_cache[url] = (photos, details)

                result["photos"] = len(photos)

                if photos:
                    result["error"] = None
                    result["needs_sign_in"] = False
                    result["title"] = result["title"] or details.get("title", "")
                    result["site"] = result["site"] or details.get("site", "")
                    result["uploader"] = result["uploader"] or details.get(
                        "uploader", ""
                    )
                    result["thumbnail_url"] = result["thumbnail_url"] or photos[0]

            except Exception as error:
                log.warning("gallery-dl lookup failed for %s: %s", url, error)
                # Only matters if yt-dlp found nothing either
                if not (result["has_video"] or result["has_audio"]):
                    result["error"] = result["error"] or core.friendly_error(error)
                    result["needs_sign_in"] = result[
                        "needs_sign_in"
                    ] or core.needs_sign_in(error)

        # 3. Thumbnail picture (sharp on high-DPI screens too)
        if result["thumbnail_url"] and not result["error"]:
            try:
                result["thumbnail"] = core.load_thumbnail(
                    result["thumbnail_url"], max_size=(400, 400)
                )
            except Exception as error:
                log.info("No thumbnail for %s: %s", url, error)  # that's fine

    def set_message(self, text, color=BAD):

        self.preview_message.setText(text)
        paint(self.preview_message, color)
        self.preview_message.setVisible(bool(text))

    def show_empty_preview(self):

        self.lookup = None
        self.loading_bar.stop()
        self.loading_bar.hide()
        self.set_thumbnail(None, FluentIcon.VIDEO)
        self.preview_title.setText("No link yet")
        self.preview_meta.setText(
            "Paste a link above. Details, sizes and qualities appear here."
        )
        self.preview_badges.set_tags([])
        self.set_message("")
        self.refresh_button.hide()
        self.hide_sign_in_panel()
        self.set_modes([VIDEO, AUDIO])
        self.set_quality_choices([])

    def show_loading_preview(self):

        # Keep the sign-in panel while retrying after a sign-in, so its
        # "Trying again…" message stays visible; otherwise hide it
        if self.sign_in_panel.level != GOOD:
            self.hide_sign_in_panel()

        self.thumbnail.set_loading(True)
        self.thumbnail_image = None
        self.loading_bar.show()
        self.loading_bar.start()
        self.preview_title.setText("Looking up the link…")
        self.preview_meta.setText("Getting title, qualities and sizes")
        self.preview_badges.set_tags([])
        self.set_message("")
        self.refresh_button.hide()
        self.quality.set_values(["Checking…"], "Checking…")
        self.quality.setEnabled(False)

        # If the site is very slow, say so (and offer ↻) instead of spinning
        request = self.lookup_request
        QTimer.singleShot(45_000, self, lambda: self.lookup_taking_long(request))

    def lookup_taking_long(self, request):

        if request == self.lookup_request and self.lookup is None:
            self.set_message(
                "This is taking longer than usual. The site may be slow: "
                "wait a bit more, or press ↻ to try again.",
                WARN,
            )
            self.refresh_button.show()

    def set_thumbnail(self, image, placeholder=FluentIcon.VIDEO):

        if image is None:
            self.thumbnail_image = None
            self.thumbnail.set_placeholder(placeholder)
            return

        self.thumbnail_image = pil_to_pixmap(image)
        self.thumbnail.set_image(self.thumbnail_image)

    def show_lookup(self, result, request):

        # Ignore answers for a link that has since been changed
        if request != self.lookup_request:
            return

        self.lookup = result
        self.loading_bar.stop()
        self.loading_bar.hide()
        self.refresh_button.show()

        try:
            self.fill_preview(result)
        except Exception:
            # Never leave the preview half-drawn or stuck
            log.exception("Showing the preview failed for %s", result.get("url"))
            self.set_thumbnail(None, FluentIcon.INFO)
            self.preview_title.setText("Couldn't show this link")
            self.set_message(
                "Something went wrong. Press ↻ to try again. "
                "(Details were saved to the error log.)"
            )

    def fill_preview(self, result):

        if result["error"]:
            needs_account = result.get("needs_sign_in")

            self.set_thumbnail(
                None, FluentIcon.PEOPLE if needs_account else FluentIcon.INFO
            )
            self.preview_title.setText(
                "This needs an account" if needs_account else "Couldn't open this link"
            )
            self.preview_meta.setText(result["url"])
            self.preview_badges.set_tags([])
            self.set_message(result["error"])
            self.set_modes([VIDEO, AUDIO])
            self.set_quality_choices([])

            # Offer sign-in right here, only when it can help
            if needs_account:
                self.show_sign_in_panel(core.site_of(result["url"]))
            else:
                self.hide_sign_in_panel()
            return

        self.hide_sign_in_panel()

        self.set_thumbnail(result["thumbnail"])
        self.preview_title.setText(result["title"] or "Untitled")

        meta = [result["site"], result["uploader"]]
        if result["duration"]:
            meta.append(human_time(result["duration"]))
        self.preview_meta.setText("  ·  ".join(part for part in meta if part))

        tags = []
        if result["videos"] > 1:
            tags.append((FluentIcon.VIDEO, f"{result['videos']} videos"))
        elif result["has_video"]:
            tags.append((FluentIcon.VIDEO, "Video"))
        if result["has_audio"] and not result["has_video"]:
            tags.append((FluentIcon.MUSIC, "Audio"))
        if result["photos"]:
            plural = "s" if result["photos"] > 1 else ""
            tags.append((FluentIcon.PHOTO, f"{result['photos']} photo{plural}"))
        self.preview_badges.set_tags(tags)
        self.set_message("")

        # Offer only what this link really has
        modes = []
        if result["has_video"]:
            modes.append(VIDEO)
        if result["has_audio"]:
            modes.append(AUDIO)
        if result["photos"]:
            modes.append(PHOTOS)
        self.set_modes(modes or [VIDEO, AUDIO])

        self.set_quality_choices(result["qualities"])

    # -------------------------------
    # DOWNLOAD AS / QUALITY
    # -------------------------------

    def set_modes(self, modes):

        current = self.mode.get()

        with QSignalBlocker(self.mode):
            self.mode.set_values(modes)
            self.mode.set(current if current in modes else modes[0])

        self.on_mode_change()

    def on_mode_change(self, value=None):

        self.update_quality_menu()
        self.update_main_button()

    def on_quality_change(self, value):

        self.preferred_height = self.qualities.get(value)

    def set_quality_choices(self, qualities):

        self.quality_list = qualities
        self.update_quality_menu()

    def update_quality_menu(self):

        mode = self.mode.get()
        lookup = self.lookup or {}

        if mode == AUDIO:
            duration = lookup.get("duration") or 0
            size = human_size(duration * MP3_KBPS * 1000 / 8) if duration else ""
            text = f"MP3 · {MP3_KBPS} kbps" + (f"  ·  ~{size}" if size else "")
            self.quality.set_values([text], text)
            self.quality.setEnabled(False)
            return

        if mode == PHOTOS:
            count = lookup.get("photos") or 0
            text = f"{count} photo{'s' if count != 1 else ''} · original quality"
            self.quality.set_values([text], text)
            self.quality.setEnabled(False)
            return

        qualities = self.quality_list
        self.quality.setEnabled(True)

        if not qualities:
            self.qualities = {BEST_QUALITY: None}
            self.quality.set_values([BEST_QUALITY], BEST_QUALITY)
            return

        # Keep the chosen height if this video has it too (the height the
        # user picked, not the menu's text: that's "Checking…" while loading)
        previous_height = self.preferred_height
        everywhere = self.plays_everywhere()

        self.qualities = {
            core.quality_text(q, everywhere): q["height"] for q in qualities
        }

        # Default: the best quality that needs no converting (in "plays
        # everywhere" mode), so a normal download is quick and just works
        no_convert = [
            core.quality_text(q, everywhere)
            for q in qualities
            if not everywhere or q.get("h264", True)
        ]
        default = (no_convert or list(self.qualities))[0]

        choice = next(
            (text for text, h in self.qualities.items() if h == previous_height),
            default,
        )
        self.quality.set_values(list(self.qualities), choice)

    # -------------------------------
    # SIGN IN  (offered only when a link needs an account)
    # -------------------------------

    def show_sign_in_panel(self, site):

        self.sign_in_site = site
        place = site or "this site"

        self.sign_in_text.setText(
            f"Continue with the browser where you're signed in to {place}. "
            "Your login is read from this PC only and never uploaded."
        )

        browsers = core.installed_browsers() or ["Firefox", "Chrome", "Edge"]

        # Firefox first: Chrome and Edge often lock their logins on Windows
        browsers.sort(key=lambda name: name != "Firefox")

        buttons = [
            (browser, lambda _=False, b=browser: self.choose_sign_in(b), index == 0)
            for index, browser in enumerate(browsers)
        ]
        buttons.append(
            ("Cookies file…", lambda: self.choose_sign_in(COOKIES_FILE), False)
        )
        self.sign_in_panel.set_buttons(buttons)

        if self.sign_in_method != NO_SIGN_IN:
            source = self.sign_in_source(self.sign_in_method)
            self.set_sign_in_status(
                f"You're signed in with {source}, but that account can't open "
                "this. Try another browser or account.",
                WARN,
            )
        else:
            self.set_sign_in_status("")

        self.sign_in_panel.show()

    def hide_sign_in_panel(self):

        self.sign_in_panel.hide()
        self.set_sign_in_status("", MUTED)

    def set_sign_in_status(self, text, color=MUTED):

        self.sign_in_panel.set_status(text, color)

    def choose_sign_in(self, method):

        cookie_file = self.cookie_file

        if method == COOKIES_FILE:
            cookie_file, _ = QFileDialog.getOpenFileName(
                self,
                "Choose your cookies.txt file",
                "",
                "Cookies file (*.txt);;All files (*.*)",
            )
            if not cookie_file:
                return  # cancelled

        self.sign_in_request += 1
        request = self.sign_in_request
        site = self.sign_in_site
        source = self.sign_in_source(method, cookie_file)

        self.sign_in_panel.set_buttons_enabled(False)
        self.set_sign_in_status(f"Checking {source}…", MUTED)

        def work():
            try:
                sites, error = core.read_logins(method, cookie_file), None
            except Exception as problem:
                log.warning("Reading logins from %s failed: %s", method, problem)
                sites, error = [], core.friendly_error(problem)

            self.ui(
                self.sign_in_checked, method, cookie_file, sites, error, site, request
            )

        threading.Thread(target=work, daemon=True).start()

    def sign_in_checked(self, method, cookie_file, sites, error, site, request):

        if request != self.sign_in_request:
            return  # the user clicked something else meanwhile

        self.sign_in_panel.set_buttons_enabled(True)
        source = self.sign_in_source(method, cookie_file)

        if error:
            self.set_sign_in_status(f"✕  {error}", BAD)
            return

        if site and site not in sites:
            domain = SITE_LOGIN_COOKIES[site][0]

            if method == COOKIES_FILE:
                advice = (
                    f"This cookies file has no {site} login. Export it again "
                    f"while signed in on {domain}."
                )
            else:
                advice = (
                    f"{method} isn't signed in to {site}. Open {domain} in "
                    f"{method}, sign in, then click {method} here again."
                )

            self.set_sign_in_status(f"✕  {advice}", BAD)
            return

        # Signed in: remember it and try the link again
        self.sign_in_method = method
        if method == COOKIES_FILE:
            self.cookie_file = cookie_file
        self.save_settings()
        self.update_sign_in_chip()

        self.set_sign_in_status(f"✓  Signed in with {source}. Trying again…", GOOD)
        self.start_lookup(force=True)

    def sign_out(self):

        self.sign_in_method = NO_SIGN_IN
        self.save_settings()
        self.update_sign_in_chip()
        self.toast("info", "Signed out", "Links are opened without an account now.")

    def update_sign_in_chip(self):

        if self.sign_in_method == NO_SIGN_IN:
            self.sign_in_chip.hide()
            return

        source = self.sign_in_source(self.sign_in_method)
        self.sign_in_chip.setText(f"Signed in · {source}")
        self.sign_in_chip.show()

    def sign_in_source(self, value, cookie_file=None):

        if value == COOKIES_FILE:
            return os.path.basename(cookie_file or self.cookie_file or "cookies file")

        return value

    # -------------------------------
    # DOWNLOAD
    # -------------------------------

    def on_main_button(self):

        if self.is_downloading:
            self.toggle_pause()
        else:
            self.start_download()

    # -------------------------------
    # PAUSE / RESUME
    # -------------------------------

    def toggle_pause(self):

        if not self.is_downloading or self.cancel_requested:
            return

        self.paused = not self.paused

        if self.paused:
            self.resume_event.clear()  # the download thread waits at its next step
            core.set_process_paused(self.helper_process, True)

            self.progress.pause()
            self.speed_label.setText("")
            self.show_status(
                f"{self.item_prefix()}Paused at {self.last_fraction:.0%}", MUTED
            )
        else:
            core.set_process_paused(self.helper_process, False)
            self.resume_event.set()

            self.progress.resume()
            self.show_status(f"{self.item_prefix()}Resuming…", MUTED)

        self.update_main_button()

    def wait_while_paused(self):
        """Called from the download thread: block here while paused."""

        while not self.resume_event.wait(0.2):
            if self.cancel_requested:
                break

        if self.cancel_requested:
            raise DownloadCancelled()

    def get_download_options(self):

        options = self.ydl_options()

        # Titles are cut to 90 bytes: TikTok/Instagram captions can be very long,
        # and Windows paths have a length limit. The id keeps names unique.
        options.update(
            {
                "outtmpl": os.path.join(
                    self.download_folder, "%(title).90B [%(id)s].%(ext)s"
                ),
                "progress_hooks": [self.progress_hook],
                "windowsfilenames": True,
            }
        )

        if self.mode.get() == AUDIO:
            options["format"] = "bestaudio/best"
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(MP3_KBPS),
                }
            ]
            return options

        # Exact pixel height of the chosen quality (None = best available)
        height = self.qualities.get(self.quality.get())

        if self.plays_everywhere():
            # At the chosen size, prefer H.264 + AAC (plays everywhere). If a
            # size has no H.264 version, it's converted after downloading.
            options["format_sort"] = ["res", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
            options["make_playable"] = True  # read by download_video_link
        else:
            # Highest resolution first; among equal ones prefer MP4/M4A streams
            options["format_sort"] = ["res", "ext:mp4:m4a"]

        nw = NO_WATERMARK

        if not self.has_ffmpeg:
            # Without ffmpeg we can only take single files with audio included
            if height:
                options["format"] = (
                    f"b[height={height}]{nw}/b[height<={height}]{nw}/b{nw}/b"
                )
            else:
                options["format"] = f"b{nw}/b"
            return options

        # Exactly the chosen height first. Other links in the list may not have
        # it, so fall back to the best below it, then to anything. Every step
        # skips watermarked versions; plain "b" is a last resort only.
        # (1440p/4K exist only as WebM on YouTube; ffmpeg puts them in MP4.)
        if height:
            options["format"] = (
                f"bv*[height={height}]{nw}+ba/b[height={height}]{nw}/"
                f"bv*[height<={height}]{nw}+ba/b[height<={height}]{nw}/"
                f"bv*{nw}+ba/b{nw}/b"
            )
        else:
            options["format"] = f"bv*{nw}+ba/b{nw}/b"

        options["merge_output_format"] = "mp4"

        return options

    def needed_space(self, urls):
        """Estimated bytes for this download (only known for a single link)."""

        lookup = self.lookup or {}

        if len(urls) != 1 or lookup.get("url") != urls[0]:
            return 0

        if self.mode.get() == AUDIO:
            return int((lookup.get("duration") or 0) * MP3_KBPS * 1000 / 8)

        if self.mode.get() == VIDEO:
            height = self.qualities.get(self.quality.get())
            sizes = [q["size"] for q in lookup.get("qualities") or []]
            chosen = [
                q["size"]
                for q in lookup.get("qualities") or []
                if q["height"] == height
            ]
            return (chosen or sizes or [0])[0]

        return 0

    def preflight(self, urls):
        """Problems to fix before downloading (empty list = good to go)."""

        links, problems = core.check_links(urls)

        if self.mode.get() == AUDIO and not self.has_ffmpeg:
            problems.append(
                "MP3 needs ffmpeg, which wasn't found. Install it "
                "(winget install Gyan.FFmpeg) and restart the app."
            )

        folder_problem = core.check_folder(
            self.download_folder, self.needed_space(links)
        )
        if folder_problem:
            problems.append(folder_problem)

        return links, problems

    def retry_failed(self):

        if self.failed_links and not self.is_downloading:
            self.start_download(self.failed_links)

    def start_download(self, links=None):

        if self.is_downloading:
            return

        urls = links or self.get_urls()

        if not urls:
            self.url_box.setFocus()
            return

        mode = self.mode.get()

        # Catch problems now, not halfway through (wrong lines, folder, space)
        urls, problems = self.preflight(urls)

        if problems:
            self.retry_button.hide()
            self.open_folder_button.hide()
            self.progress.setValue(0)
            self.progress.setError(True)
            self.speed_label.setText("")
            self.show_status("\n".join(f"•  {p}" for p in problems), BAD)
            return

        self.failed_links = []
        self.retry_button.hide()
        self.is_downloading = True
        self.cancel_requested = False
        self.paused = False
        self.resume_event.set()
        self.last_fraction = 0

        self.update_main_button()
        self.open_folder_button.hide()
        self.progress.setError(False)
        self.progress.resume()
        self.progress.setValue(0)
        self.show_status("Starting…", MUTED)

        # Read widget values here: widgets must only be touched on the UI thread
        if mode == PHOTOS:
            job, options = self.download_photo_link, self.gallery_cookie_args()
            kind = "photo"
        else:
            job, options = self.download_video_link, self.get_download_options()
            kind = "file"

        stop_on_error = self.on_error_menu.get() == STOP_ON_ERROR

        threading.Thread(
            target=self.download_all,
            args=(urls, job, options, kind, stop_on_error),
            daemon=True,
        ).start()

    def cancel_download(self):

        if not self.is_downloading or self.cancel_requested:
            return

        self.cancel_requested = True
        self.show_status("Cancelling…", MUTED)
        self.update_main_button()

        # Wake a paused download so it can stop
        self.resume_event.set()

        if self.helper_process and self.helper_process.poll() is None:
            core.set_process_paused(self.helper_process, False)
            self.helper_process.terminate()

    def download_all(self, urls, job, options, kind, stop_on_error):
        """Download each link in turn (runs on a background thread)."""

        saved, failed = 0, []  # failed: [(link, reason)]

        try:
            for index, url in enumerate(urls):
                if self.cancel_requested:
                    break

                self.current_item = (index + 1, len(urls))

                try:
                    saved += job(url, options)

                except Exception as error:
                    if self.cancel_requested:
                        break

                    log.warning("Download failed for %s: %s", url, error)
                    failed.append((url, core.friendly_error(error)))

                    if stop_on_error:
                        # The rest weren't tried: offer them in "Retry" too
                        for skipped in urls[index + 1 :]:
                            failed.append(
                                (skipped, "Not downloaded: stopped after an error.")
                            )
                        break

        except Exception:
            # Our own bug: report it instead of leaving the app "downloading"
            log.exception("Download loop crashed")
            failed.append(("", "Something went wrong. Details were saved to the log."))

        finally:
            self.ui(self.download_finished, saved, failed, kind)

    def download_video_link(self, url, options):

        options = dict(options)
        make_playable = options.pop("make_playable", False)

        with yt_dlp.YoutubeDL(options) as downloader:
            # Clean social-media titles before the file is named
            downloader.add_post_processor(CleanTitle(), when="pre_process")

            # After merging: convert AV1/VP9/HEVC to H.264 so the picture shows
            if make_playable and self.has_ffmpeg:
                downloader.add_post_processor(MakePlayable(self.convert_video))

            downloader.download([url])

        return 1

    def convert_video(self, path, duration):
        """Runs on the download thread: convert to H.264 with progress."""

        prefix = self.item_prefix()

        def progress(fraction):
            text = f"{prefix}Converting so it plays everywhere  {fraction:.0%}"
            self.ui(self.update_progress, fraction, text, "")

        def start(command):
            # Kept in helper_process so Pause / Cancel work while converting
            self.helper_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=NO_WINDOW,
            )
            return self.helper_process

        self.ui(
            self.update_progress, 0, f"{prefix}Converting so it plays everywhere…", ""
        )

        try:
            core.convert_to_h264(path, duration, progress, start)
        finally:
            self.helper_process = None

        if self.cancel_requested:
            raise DownloadCancelled()

    def download_photo_link(self, url, cookie_args):

        return self.download_photos(url, cookie_args)

    def download_photos(self, url, cookie_args):

        # Reuse what the preview found; otherwise look the post up first
        if url not in self.photo_cache:
            self.ui(self.show_status, "Finding photos…", MUTED)
            photos, meta = core.find_photos(url, cookie_args)
            self.photo_cache[url] = (photos, core.post_details(meta) if meta else {})

        photos, details = self.photo_cache[url]

        if not photos:
            raise RuntimeError("There are no photos in this post.")

        # Each post gets its own folder: "<title> [<id>]"
        folder_name = core.safe_name(
            f"{details.get('title') or 'Photos'} [{details.get('id', 'post')}]"
        )
        target = os.path.join(self.download_folder, folder_name)

        command, options = core.run_gallery_dl(
            [
                "-D",
                target,
                "-o",
                "videos=false",
                "-o",
                "audio=false",
                *cookie_args,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self.helper_process = subprocess.Popen(command, **options)
        total, done, output = len(photos), 0, []

        # gallery-dl prints one line per saved file ("# path" if it already existed)
        for line in self.helper_process.stdout:
            line = line.strip()
            output.append(line)

            if line.startswith(("# ", target)) or os.path.isabs(line):
                done += 1
                self.ui(self.update_photo_progress, done, total)

        self.helper_process.wait()

        if self.cancel_requested:
            raise DownloadCancelled()

        if self.helper_process.returncode and not done:
            raise RuntimeError(core.gallery_error("\n".join(output)))

        return done

    def ui(self, function, *args, **kwargs):
        """Run a function on the UI thread. Safe to call from any thread."""

        try:
            self.dispatcher.call.emit((function, args, kwargs))
        except RuntimeError:
            pass  # the window is already gone (app closing)

    def run_ui_call(self, call):

        function, args, kwargs = call
        try:
            function(*args, **kwargs)
        except Exception:
            log.exception("UI update failed")

    # -------------------------------
    # PROGRESS
    # -------------------------------

    def item_prefix(self):

        index, total = self.current_item
        return f"{index} of {total}  ·  " if total > 1 else ""

    def progress_hook(self, data):

        # yt-dlp calls this between chunks: waiting here pauses the download.
        # If the site drops the connection meanwhile, yt-dlp resumes from the
        # bytes it already has.
        self.wait_while_paused()

        if self.cancel_requested:
            raise DownloadCancelled()

        if data["status"] == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0

            fraction = downloaded / total if total else 0
            speed = data.get("speed")
            eta = data.get("eta")

            details = []
            if total:
                details.append(f"{human_size(downloaded)} of {human_size(total)}")
            if speed:
                details.append(f"{human_size(speed)}/s")
            if eta:
                details.append(f"{human_time(eta)} left")

            text = f"{self.item_prefix()}Downloading {fraction:.0%}"
            self.ui(self.update_progress, fraction, text, "  ·  ".join(details))

        elif data["status"] == "finished":
            text = f"{self.item_prefix()}Processing file…"
            self.ui(self.update_progress, 1, text, "")

    def update_progress(self, fraction, text, details):

        # Updates already queued when Pause was pressed shouldn't undo "Paused"
        if not self.is_downloading or self.cancel_requested or self.paused:
            return

        self.last_fraction = fraction
        self.progress.setValue(int(fraction * 1000))
        self.show_status(text, MUTED)
        self.speed_label.setText(details)

    def update_photo_progress(self, done, total):

        fraction = done / total if total else 0
        text = f"{self.item_prefix()}Saving photos {done} of {total}"
        self.update_progress(min(fraction, 1), text, "")

    def show_status(self, text, color):

        self.status_area.show()
        self.status_label.setText(text)
        paint(self.status_label, color if color != MUTED else theme.TEXT)

    def on_unexpected_error(self, exc_type, exc_value, exc_traceback):
        """Called for crashes in button clicks and other UI callbacks."""

        log.error("Unexpected error", exc_info=(exc_type, exc_value, exc_traceback))
        self.show_status(
            "Something went wrong. Try again; if it keeps happening, open "
            "Settings → Error log.",
            BAD,
        )

    def download_finished(self, saved, failed, kind):

        errors = [reason for _, reason in failed]
        self.failed_links = [link for link, _ in failed if link]

        cancelled = self.cancel_requested

        self.is_downloading = False
        self.cancel_requested = False
        self.paused = False
        self.resume_event.set()
        self.helper_process = None
        self.progress.resume()
        self.update_main_button()
        self.speed_label.setText("")

        files = f"{saved} {kind}{'s' if saved != 1 else ''}"

        if cancelled:
            text = "Download cancelled." + (f" {files} saved." if saved else "")
            self.show_status(text, MUTED)
        elif errors:
            self.progress.setValue(0)
            self.progress.setError(True)
            reasons = "\n".join(f"•  {reason}" for reason in dict.fromkeys(errors))
            head = f"{files} saved, {len(errors)} failed:\n" if saved else ""
            self.show_status(head + reasons, BAD)
            self.toast(
                "error",
                "Some downloads failed" if saved else "Download failed",
                f"{files} saved, {len(errors)} failed." if saved else errors[0],
                duration=6000,
            )
        else:
            self.progress.setValue(1000)
            self.show_status(f"✓  Done. {files} saved.", GOOD)
            self.toast("success", "Download complete", f"{files} saved.")

        if self.failed_links and not cancelled:
            count = len(self.failed_links)
            self.retry_button.setText(
                "Retry failed" if count == 1 else f"Retry {count} failed"
            )
            self.retry_button.show()

        if saved:
            self.open_folder_button.show()

        # Flash the taskbar button if the user is in another window
        QApplication.alert(self)

    # -------------------------------
    # UPDATES
    # -------------------------------

    def check_for_updates(self, manual=False):

        if manual:
            self.show_home()
            self.show_update_banner("Checking for updates…", can_update=False)

        def work():
            try:
                release, error = core.fetch_latest_release(), None
            except Exception as problem:
                release, error = None, problem

            self.ui(self.show_update_result, release, error, manual)

        threading.Thread(target=work, daemon=True).start()

    def show_update_result(self, release, error, manual):

        newer = release and core.parse_version(release["version"]) > core.parse_version(
            APP_VERSION
        )

        if newer:
            self.update_release = release
            size = human_size(release["size"])
            self.show_update_banner(
                f"Version {release['version']} is available "
                f"(you have {APP_VERSION}).  {size}",
                can_update=True,
            )
        elif not manual:
            return  # quiet check: say nothing if there's no update
        elif error:
            self.show_update_banner(
                "Couldn't check for updates. Are you online?", can_update=False
            )
        else:
            self.show_update_banner(
                f"You're up to date (version {APP_VERSION}).", can_update=False
            )

    def show_update_banner(self, text, can_update):

        self.update_label.setText(text)
        self.later_button.setText("Later" if can_update else "Close")
        self.later_button.setEnabled(True)
        self.update_button.setEnabled(True)
        self.update_button.setVisible(can_update)
        self.update_banner.show()

    def hide_update_banner(self):

        self.update_banner.hide()

    def start_update(self):

        release = self.update_release

        if not release:
            return

        if self.is_downloading:
            self.update_label.setText(
                "Finish or cancel the current download first, then update."
            )
            return

        if not FROZEN:
            self.update_label.setText(
                "Running from source: updates install only in the installed "
                "app. Use git pull instead."
            )
            return

        self.update_button.setEnabled(False)
        self.later_button.setEnabled(False)
        self.update_label.setText("Downloading update…")

        def on_progress(done, total):
            if total:
                self.ui(
                    self.update_label.setText,
                    f"Downloading update…  {done / total:.0%}",
                )

        def work():
            try:
                installer = core.download_update(release, on_progress)
            except Exception as error:
                self.ui(self.update_failed, str(error))
                return

            self.ui(self.install_update, installer)

        threading.Thread(target=work, daemon=True).start()

    def update_failed(self, message):

        self.update_label.setText(f"Update failed: {message}")
        self.update_button.setEnabled(True)
        self.later_button.setEnabled(True)

    def install_update(self, installer):

        self.update_label.setText("Installing… the app will reopen.")
        QApplication.processEvents()

        # Silent install over the current version; the installer reopens the
        # app when it's done (see the [Run] section of installer.iss)
        subprocess.Popen(
            [
                installer,
                "/SILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
            ]
        )

        self.save_settings()
        QApplication.quit()

    # -------------------------------
    # CLOSING
    # -------------------------------

    def closeEvent(self, event):

        if self.is_downloading:
            box = MessageBox(
                "Download in progress",
                "A download is running. Cancel it and quit?",
                self,
            )
            box.yesButton.setText("Cancel and quit")
            box.cancelButton.setText("Keep downloading")

            if not box.exec():
                event.ignore()
                return

            self.cancel_download()

        self.save_settings()
        event.accept()
        super().closeEvent(event)
