"""The main window: a Fluent (Windows 11 style) app with a sidebar.

Pages: Home (paste links, start downloads), Downloads (current and recent)
and Settings. Background threads never touch widgets; they hand work to the
UI thread with self.ui(...).
"""

import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time

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
    InfoBadge,
    InfoBadgePosition,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    NavigationItemPosition,
    PlainTextEdit,
    PrimaryPushButton,
    PrimaryPushSettingCard,
    PushButton,
    PushSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
    ToolTipFilter,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
    setCustomStyleSheet,
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
from .downloads import (
    CANCELLED,
    DONE,
    DOWNLOADING,
    FAILED,
    PAUSED,
    QUEUED,
    DownloadRow,
    DownloadTask,
    load_history,
    save_history,
)
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
MAX_PARALLEL = 3  # downloads running at once; the rest wait their turn


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
        # Quality the user picked ("1080p"), kept for the next links
        self.preferred_quality = None
        self.quality_names = {}  # menu text -> "1080p"

        # Sign-in
        self.cookie_file = self.settings.get("cookie_file")
        self.sign_in_method = NO_SIGN_IN  # a browser name, COOKIES_FILE or none
        self.sign_in_request = 0
        self.sign_in_site = None

        # Downloads: running/waiting ones, and finished ones (newest first)
        self.tasks = []
        self.history = load_history()
        self.batches = itertools.count(1)
        self.downloads_badge = None
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
        self.downloads_page = self.create_downloads_page()
        self.settings_page = self.create_settings_page()

        self.addSubInterface(self.home_page, FluentIcon.HOME, "Home")
        self.addSubInterface(self.downloads_page, FluentIcon.DOWNLOAD, "Downloads")
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
            "Ctrl+Return": self.start_download,
            "Ctrl+L": self.focus_links,
            "Ctrl+J": self.show_downloads,  # as in web browsers
        }
        for keys, action in shortcuts.items():
            QShortcut(QKeySequence(keys), self, action)

        self.update_main_button()
        self.refresh_downloads()

    def create_home_page(self):

        area, layout = page("home")

        title = TitleLabel("New download", area)
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
        row.addWidget(
            muted(CaptionLabel("Ctrl+Enter to download  ·  Ctrl+J downloads", widget))
        )

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

        # Always "Download": downloads run on the Downloads page, so Home is
        # free for the next link straight away
        self.main_button = PrimaryPushButton(FluentIcon.DOWNLOAD, "Download")
        self.main_button.setFixedHeight(44)
        font = self.main_button.font()
        font.setPixelSize(15)
        font.setBold(True)
        self.main_button.setFont(font)
        self.main_button.setIconSize(QSize(18, 18))
        self.main_button.clicked.connect(self.start_download)
        actions.addWidget(self.main_button, 1)

        layout.addSpacing(2)
        layout.addLayout(actions)

        # "Download started" (with a way to the Downloads page), or what to
        # fix before downloading. Hidden until then.
        self.status_area, status = card(
            QHBoxLayout, spacing=12, margins=(20, 14, 14, 14)
        )

        self.status_label = BodyLabel(self.status_area)
        self.status_label.setWordWrap(True)
        status.addWidget(self.status_label, 1)

        self.go_to_downloads_button = PushButton(
            FluentIcon.RIGHT_ARROW, "Go to Downloads", self.status_area
        )
        self.go_to_downloads_button.clicked.connect(self.show_downloads)
        status.addWidget(self.go_to_downloads_button, 0, Qt.AlignmentFlag.AlignTop)

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

    def create_downloads_page(self):
        """Current downloads (with progress) and recent ones (to open)."""

        area, layout = page("downloads")
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(TitleLabel("Downloads", area))
        head.addStretch(1)
        open_folder = PushButton(FluentIcon.FOLDER, "Open folder", area)
        open_folder.clicked.connect(self.open_folder)
        head.addWidget(open_folder, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)
        layout.addSpacing(6)

        actions = {
            "pause": self.toggle_pause,
            "cancel": self.cancel_task,
            "open": self.open_task_file,
            "folder": self.show_task_in_folder,
            "retry": self.retry_task,
            "remove": self.remove_task,
        }
        self.row_actions = actions

        # --- Current ---
        current_head = QHBoxLayout()
        self.current_title = SubtitleLabel("Current downloads", area)
        current_head.addWidget(self.current_title)
        current_head.addStretch(1)
        layout.addLayout(current_head)

        self.current_list = QVBoxLayout()
        self.current_list.setSpacing(8)
        layout.addLayout(self.current_list)
        self.current_rows = {}  # task id -> row

        self.current_empty, empty = card(
            QHBoxLayout, margins=(20, 16, 14, 16), spacing=12
        )
        empty.addWidget(
            muted(
                BodyLabel(
                    "Nothing is downloading. Paste a link on Home to start.",
                    self.current_empty,
                )
            ),
            1,
        )
        go_home = PushButton(FluentIcon.HOME, "Go to Home", self.current_empty)
        go_home.clicked.connect(self.focus_links)
        empty.addWidget(go_home)
        layout.addWidget(self.current_empty)

        layout.addSpacing(16)

        # --- Recent ---
        recent_head = QHBoxLayout()
        self.recent_title = SubtitleLabel("Recent downloads", area)
        recent_head.addWidget(self.recent_title)
        recent_head.addStretch(1)
        self.retry_all_button = PushButton(FluentIcon.SYNC, "Retry failed", area)
        self.retry_all_button.clicked.connect(self.retry_all_failed)
        recent_head.addWidget(self.retry_all_button)
        self.clear_history_button = HyperlinkButton(area)
        self.clear_history_button.setText("Clear list")
        self.clear_history_button.setToolTip("Your files stay where they are")
        self.clear_history_button.clicked.connect(self.clear_history)
        recent_head.addWidget(self.clear_history_button)
        layout.addLayout(recent_head)

        self.recent_list = QVBoxLayout()
        self.recent_list.setSpacing(8)
        layout.addLayout(self.recent_list)
        self.recent_rows = {}

        self.recent_empty = muted(
            BodyLabel("Downloads you finish will show up here.", area)
        )
        self.recent_empty.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.recent_empty)

        layout.addStretch(1)
        return area

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
            # The card's labels are plain QLabels (no setTextColor), so the
            # colour goes in the card's own light/dark stylesheet
            setCustomStyleSheet(
                warning,
                *(f"QLabel#contentLabel {{ color: {color}; }}" for color in WARN),
            )
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

    def show_downloads(self):

        self.switchTo(self.downloads_page)

    def focus_links(self):

        self.show_home()
        self.url_box.setFocus()

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

        self.preferred_quality = self.quality_names.get(value)

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
            self.quality_names = {}
            self.quality.set_values([BEST_QUALITY], BEST_QUALITY)
            return

        # Keep the chosen quality if this video has it too. By name, not
        # pixels: 1080p is 1920x1080 on one video and 1920x804 on another.
        # (Not the menu's text either: that's "Checking…" while loading.)
        previous = self.preferred_quality
        everywhere = self.plays_everywhere()

        # Menu text -> the real height, which picks the file to download
        self.qualities = {
            core.quality_text(q, everywhere): q["height"] for q in qualities
        }
        self.quality_names = {
            core.quality_text(q, everywhere): q["label"] for q in qualities
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
            (text for text, name in self.quality_names.items() if name == previous),
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

    @property
    def is_downloading(self):
        """Is anything running or waiting to run?"""

        return any(task.active for task in self.tasks)

    def get_download_options(self, mode=None, height=...):
        """yt-dlp options for a video / audio download. Without arguments it
        uses what's chosen in the window."""

        mode = mode or self.mode.get()
        if height is ...:
            height = self.qualities.get(self.quality.get())

        options = self.ydl_options()

        # Titles are cut to 90 bytes: TikTok/Instagram captions can be very long,
        # and Windows paths have a length limit. The id keeps names unique.
        options.update(
            {
                "outtmpl": os.path.join(
                    self.download_folder, "%(title).90B [%(id)s].%(ext)s"
                ),
                "windowsfilenames": True,
            }
        )

        if mode == AUDIO:
            options["format"] = "bestaudio/best"
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(MP3_KBPS),
                }
            ]
            return options

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

    def start_download(self):
        """Download button: queue the links, then clear Home for the next ones."""

        urls = self.get_urls()

        if not urls:
            self.url_box.setFocus()
            return

        # Catch problems now, not halfway through (wrong lines, folder, space)
        urls, problems = self.preflight(urls)

        if problems:
            self.show_status("\n".join(f"•  {p}" for p in problems), BAD)
            return

        mode = self.mode.get()
        height = self.qualities.get(self.quality.get()) if mode == VIDEO else None
        quality = self.quality_names.get(self.quality.get(), "Best")

        # What the preview already knows about the first link
        details = {}
        lookup = self.lookup or {}
        if lookup.get("url") in urls and not lookup.get("error"):
            details[lookup["url"]] = {
                "title": lookup.get("title"),
                "site": lookup.get("site"),
                "thumbnail": lookup.get("thumbnail"),
                "thumbnail_url": lookup.get("thumbnail_url"),
            }

        tasks = self.queue_downloads(urls, mode, height, quality, details)
        self.show_started(tasks)

        # Ready for the next link
        self.url_box.clear()
        self.url_box.setFocus()

    def queue_downloads(self, urls, mode, height, quality, details=None, job=None):
        """Add one download per link; up to MAX_PARALLEL run at once.

        job(task) -> files saved. Normally picked from the mode (tests pass
        their own)."""

        details = details or {}
        batch = next(self.batches)
        stop_on_error = self.on_error_menu.get() == STOP_ON_ERROR

        # Read settings here: widgets must only be touched on the UI thread
        if mode == PHOTOS:
            job, options = job or self.download_photos, self.gallery_cookie_args()
        else:
            job = job or self.download_video_link
            options = self.get_download_options(mode, height)

        tasks = []
        for url in urls:
            task = DownloadTask(url, mode, height, quality, **details.get(url, {}))
            task.job, task.options = job, options
            task.batch, task.stop_on_error = batch, stop_on_error
            self.tasks.append(task)
            tasks.append(task)

        self.refresh_downloads()
        self.start_next_downloads()
        return tasks

    def start_next_downloads(self):

        running = sum(1 for task in self.tasks if task.running)

        for task in self.tasks:
            if running >= MAX_PARALLEL:
                break

            if task.state == QUEUED and not task.running:
                task.running = True
                task.state = DOWNLOADING
                task.status = "Starting…"
                running += 1
                threading.Thread(
                    target=self.run_task, args=(task,), daemon=True
                ).start()
                self.refresh_row(task)

    def run_task(self, task):
        """Download one link (runs on a background thread)."""

        saved, error = 0, None

        try:
            saved = task.job(task)

        except Exception as problem:
            if not task.cancel_requested:
                log.warning("Download failed for %s: %s", task.url, problem)
                error = core.friendly_error(problem)

        finally:
            self.ui(self.task_finished, task, saved, error)

    def task_finished(self, task, saved, error):

        task.running = False
        task.helper_process = None
        task.saved = saved
        task.finished_at = time.time()

        if task.cancel_requested:
            task.state = CANCELLED
        elif error:
            task.state, task.error = FAILED, error
        else:
            task.state, task.fraction = DONE, 1.0
            if task.path and os.path.isfile(task.path):
                task.size = os.path.getsize(task.path)

        self.move_to_history(task)

        # "Stop" setting: the rest of the links from the same click don't run
        if task.state == FAILED and task.stop_on_error:
            for other in list(self.tasks):
                if other.batch == task.batch and other.state == QUEUED:
                    other.state = FAILED
                    other.error = "Not downloaded: stopped after an error."
                    other.finished_at = time.time()
                    self.move_to_history(other)

        self.refresh_downloads()
        self.start_next_downloads()

        if not any(t.batch == task.batch for t in self.tasks):
            self.batch_finished(task.batch)

    def move_to_history(self, task):

        if task in self.tasks:
            self.tasks.remove(task)
        self.history.insert(0, task)
        save_history(self.history)

    def batch_finished(self, batch):
        """All links from one Download click are done: say how it went."""

        tasks = [t for t in self.history if t.batch == batch]
        done = [t for t in tasks if t.state == DONE]
        failed = [t for t in tasks if t.state == FAILED]

        if not done and not failed:
            return  # all cancelled: the user knows

        if failed:
            if len(tasks) == 1:
                self.toast("error", "Download failed", failed[0].error, 6000)
            else:
                self.toast(
                    "error",
                    "Some downloads failed",
                    f"{len(done)} saved, {len(failed)} failed.",
                    6000,
                )
        elif len(done) == 1:
            self.toast("success", "Download complete", self.short_title(done[0]))
        else:
            self.toast("success", "Downloads complete", f"{len(done)} saved.")

        # Flash the taskbar button if the user is in another window
        QApplication.alert(self)

    @staticmethod
    def short_title(task, length=60):

        name = task.name()
        return name if len(name) <= length else name[: length - 1] + "…"

    # --- the Downloads page ---

    def refresh_downloads(self):
        """Make the lists match self.tasks / self.history."""

        self.sync_rows(self.current_list, self.current_rows, self.tasks)
        self.sync_rows(self.recent_list, self.recent_rows, self.history)

        active = len(self.tasks)
        self.current_title.setText(
            f"Current downloads ({active})" if active else "Current downloads"
        )
        self.current_empty.setVisible(not active)
        self.recent_empty.setVisible(not self.history)
        self.clear_history_button.setVisible(bool(self.history))
        self.retry_all_button.setVisible(any(t.state == FAILED for t in self.history))
        self.update_downloads_badge(active)

    def sync_rows(self, layout, rows, tasks):

        wanted = {task.id for task in tasks}

        for task_id in [i for i in rows if i not in wanted]:
            row = rows.pop(task_id)
            layout.removeWidget(row)
            row.deleteLater()

        for index, task in enumerate(tasks):
            row = rows.get(task.id)
            if row is None:
                row = DownloadRow(task, self.row_actions)
                rows[task.id] = row
            if layout.indexOf(row) != index:
                layout.insertWidget(index, row)
            row.refresh()

    def refresh_row(self, task):

        row = self.current_rows.get(task.id) or self.recent_rows.get(task.id)
        if row:
            row.refresh()

    def update_downloads_badge(self, count):
        """A count on the Downloads item in the sidebar while things run."""

        if count and self.downloads_badge is None:
            item = self.navigationInterface.widget(self.downloads_page.objectName())
            self.downloads_badge = InfoBadge.attension(
                str(count),
                parent=item.parent(),
                target=item,
                position=InfoBadgePosition.NAVIGATION_ITEM,
            )
        elif self.downloads_badge is not None:
            self.downloads_badge.setText(str(count))
            self.downloads_badge.adjustSize()

        if self.downloads_badge is not None:
            self.downloads_badge.setVisible(bool(count))

    # --- buttons on a download ---

    def toggle_pause(self, task):

        if not task.running or task.cancel_requested:
            return

        if task.state == PAUSED:
            core.set_process_paused(task.helper_process, False)
            task.resume_event.set()
            task.state = DOWNLOADING
            task.status = "Resuming…"
        else:
            task.state = PAUSED
            task.resume_event.clear()  # the download thread waits at its next step
            core.set_process_paused(task.helper_process, True)
            task.status = f"Paused at {task.fraction:.0%}"

        self.refresh_row(task)

    def cancel_task(self, task):

        if not task.active or task.cancel_requested:
            return

        task.cancel_requested = True

        if not task.running:
            # Hadn't started yet: nothing to stop
            self.task_finished(task, 0, None)
            return

        task.status = "Cancelling…"
        task.resume_event.set()  # wake a paused download so it can stop

        process = task.helper_process
        if process and process.poll() is None:
            core.set_process_paused(process, False)
            process.terminate()

        self.refresh_row(task)

    def cancel_all(self):

        for task in list(self.tasks):
            self.cancel_task(task)

    def open_task_file(self, task):

        if task.path and os.path.exists(task.path):
            os.startfile(task.path)
        else:
            self.toast(
                "warning",
                "File not found",
                "It may have been moved or deleted.",
            )
            self.refresh_row(task)

    def show_task_in_folder(self, task):

        path = task.path
        if path and os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif path and os.path.isdir(path):
            os.startfile(path)
        else:
            self.open_folder()

    def retry_task(self, task):

        self.retry([task])

    def retry_all_failed(self):

        self.retry([t for t in self.history if t.state == FAILED])

    def retry(self, tasks):
        """Download these again, with the same choices as the first time."""

        for task in tasks:
            if task in self.history:
                self.history.remove(task)

        details = {}
        for task in tasks:
            details[task.url] = {
                "title": task.title,
                "site": task.site,
                "thumbnail": task.thumbnail,
                "thumbnail_url": task.thumbnail_url,
            }

        # Links that wanted the same thing go together
        groups = {}
        for task in tasks:
            key = (task.mode, task.height, task.quality)
            groups.setdefault(key, []).append(task.url)

        for (mode, height, quality), urls in groups.items():
            self.queue_downloads(urls, mode, height, quality, details)

        save_history(self.history)
        self.refresh_downloads()

    def remove_task(self, task):

        if task in self.history:
            self.history.remove(task)
            save_history(self.history)
            self.refresh_downloads()

    def clear_history(self):

        self.history = []
        save_history(self.history)
        self.refresh_downloads()

    # --- what Home shows after Download ---

    def show_started(self, tasks):

        if len(tasks) == 1:
            text = f"✓  Download started: {self.short_title(tasks[0])}"
        else:
            text = f"✓  {len(tasks)} downloads started"

        waiting = sum(1 for t in self.tasks if t.state == QUEUED)
        if waiting:
            text += (
                f"\n{waiting} waiting for their turn (up to {MAX_PARALLEL} at once)."
            )

        self.status_label.setText(text)
        paint(self.status_label, GOOD)
        self.go_to_downloads_button.show()
        self.status_area.show()

    def show_status(self, text, color):
        """A message under the Download button (problems to fix, errors)."""

        self.status_label.setText(text)
        paint(self.status_label, color if color != MUTED else theme.TEXT)
        self.go_to_downloads_button.hide()
        self.status_area.show()

    def on_unexpected_error(self, exc_type, exc_value, exc_traceback):
        """Called for crashes in button clicks and other UI callbacks."""

        log.error("Unexpected error", exc_info=(exc_type, exc_value, exc_traceback))
        self.show_status(
            "Something went wrong. Try again; if it keeps happening, open "
            "Settings → Error log.",
            BAD,
        )

    # --- the work itself (background threads) ---

    def download_video_link(self, task):

        options = dict(task.options)
        make_playable = options.pop("make_playable", False)
        options["progress_hooks"] = [lambda data: self.progress_hook(task, data)]

        with yt_dlp.YoutubeDL(options) as downloader:
            # Clean social-media titles before the file is named
            downloader.add_post_processor(CleanTitle(), when="pre_process")

            # After merging: convert AV1/VP9/HEVC to H.264 so the picture shows
            if make_playable and self.has_ffmpeg:
                downloader.add_post_processor(
                    MakePlayable(
                        lambda path, duration: self.convert_video(task, path, duration)
                    )
                )

            info = downloader.extract_info(task.url, download=True) or {}

        # Where the finished file is (after merging / converting to MP3)
        downloads = info.get("requested_downloads") or [info]
        task.path = downloads[0].get("filepath") or info.get("filepath")
        return 1

    def convert_video(self, task, path, duration):
        """Runs on the download thread: convert to H.264 with progress."""

        def progress(fraction):
            text = f"Converting so it plays everywhere  ·  {fraction:.0%}"
            self.ui(self.update_task_progress, task, fraction, text)

        def start(command):
            # Kept on the task so Pause / Cancel work while converting
            task.helper_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=NO_WINDOW,
            )
            return task.helper_process

        self.ui(
            self.update_task_progress, task, 0, "Converting so it plays everywhere…"
        )

        try:
            core.convert_to_h264(path, duration, progress, start)
        finally:
            task.helper_process = None

        if task.cancel_requested:
            raise DownloadCancelled()

    def download_photos(self, task):

        url, cookie_args = task.url, task.options

        # Reuse what the preview found; otherwise look the post up first
        if url not in self.photo_cache:
            self.ui(self.update_task_progress, task, 0, "Finding photos…")
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
        task.path = target

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

        task.helper_process = subprocess.Popen(command, **options)
        total, done, output = len(photos), 0, []

        # gallery-dl prints one line per saved file ("# path" if it already existed)
        for line in task.helper_process.stdout:
            line = line.strip()
            output.append(line)

            if line.startswith(("# ", target)) or os.path.isabs(line):
                done += 1
                fraction = min(done / total, 1) if total else 0
                text = f"Saving photos  ·  {done} of {total}"
                self.ui(self.update_task_progress, task, fraction, text)

        task.helper_process.wait()

        if task.cancel_requested:
            raise DownloadCancelled()

        if task.helper_process.returncode and not done:
            raise RuntimeError(core.gallery_error("\n".join(output)))

        return done

    def wait_while_paused(self, task):
        """Called from the download thread: block here while paused."""

        while not task.resume_event.wait(0.2):
            if task.cancel_requested:
                break

        if task.cancel_requested:
            raise DownloadCancelled()

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

    def progress_hook(self, task, data):

        # yt-dlp calls this between chunks: waiting here pauses the download.
        # If the site drops the connection meanwhile, yt-dlp resumes from the
        # bytes it already has.
        self.wait_while_paused(task)

        # Links added without a preview get their title and picture now
        info = data.get("info_dict") or {}
        if not task.asked_details and info:
            task.asked_details = True
            self.ui(self.set_task_details, task, info)

        if data["status"] == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0

            fraction = downloaded / total if total else 0
            speed = data.get("speed")
            eta = data.get("eta")

            details = [f"{fraction:.0%}"]
            if total:
                details.append(f"{human_size(downloaded)} of {human_size(total)}")
            if speed:
                details.append(f"{human_size(speed)}/s")
            if eta:
                details.append(f"{human_time(eta)} left")

            self.ui(self.update_task_progress, task, fraction, "  ·  ".join(details))

        elif data["status"] == "finished":
            self.ui(self.update_task_progress, task, 1, "Finishing…")

    def update_task_progress(self, task, fraction, text):

        # Updates already queued when Pause was pressed shouldn't undo "Paused"
        if not task.running or task.cancel_requested or task.state == PAUSED:
            return

        task.fraction = fraction
        task.status = text
        self.refresh_row(task)

    def set_task_details(self, task, info):

        task.title = task.title or core.clean_title(info) or info.get("title") or ""
        task.site = task.site or core.site_name(info)
        url = info.get("thumbnail")

        if url and task.thumbnail is None:
            task.thumbnail_url = url

            def load():
                try:
                    picture = core.load_thumbnail(url, max_size=(224, 126))
                except Exception:
                    return  # no picture is fine
                self.ui(self.set_task_picture, task, picture)

            threading.Thread(target=load, daemon=True).start()

        self.refresh_row(task)

    def set_task_picture(self, task, picture):

        task.thumbnail = picture
        self.refresh_row(task)

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
                "Finish or cancel the current downloads first, then update."
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
            count = len(self.tasks)
            box = MessageBox(
                "Downloads in progress",
                (
                    "A download is still running. Cancel it and quit?"
                    if count == 1
                    else f"{count} downloads are still running. Cancel them and quit?"
                ),
                self,
            )
            box.yesButton.setText("Cancel and quit")
            box.cancelButton.setText("Keep downloading")

            if not box.exec():
                event.ignore()
                return

            self.cancel_all()

        self.save_settings()
        event.accept()
        super().closeEvent(event)
