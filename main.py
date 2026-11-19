import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from tkinter import filedialog, messagebox

import customtkinter as ctk
import yt_dlp
from PIL import Image
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled

# -------------------------------
# THEME  (each colour is a (light, dark) pair)
# -------------------------------

BG = ("#f6f6f7", "#111113")
CARD = ("#ffffff", "#1a1a1e")
FIELD = ("#f1f2f4", "#24242a")
FIELD_HOVER = ("#e7e8eb", "#2d2d34")
BORDER = ("#e7e8eb", "#2a2a31")
TEXT = ("#16171a", "#ececf1")
MUTED = ("#6b7280", "#9a9eaa")
FAINT = ("#a3a8b3", "#5d616c")
ACCENT = ("#e5484d", "#e5484d")
ACCENT_HOVER = ("#cf3a3f", "#f0585d")
GOOD = ("#15803d", "#4ade80")
BAD = ("#b91c1c", "#f87171")
WARN = ("#b45309", "#fbbf24")
FONT = "Segoe UI"

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".video_downloader.json")

APP_VERSION = "1.0.0"

# -------------------------------
# RUNNING AS AN .EXE (PyInstaller)
# -------------------------------

FROZEN = getattr(sys, "frozen", False)

# Folder of the .exe (installed app) or of this file (running from source)
APP_DIR = (
    os.path.dirname(sys.executable)
    if FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)

# Bundled read-only files (icon); PyInstaller unpacks them to sys._MEIPASS
RESOURCE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
ICON_FILE = os.path.join(RESOURCE_DIR, "assets", "icon.ico")


def use_bundled_tools():
    """Put the bundled ffmpeg and Deno (tools\\...) first on PATH.

    The installer ships them, so the app works on PCs that have neither.
    """

    for tool in ("ffmpeg", "deno"):
        folder = os.path.join(APP_DIR, "tools", tool)

        if os.path.isdir(folder):
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


use_bundled_tools()

# -------------------------------
# OPTIONS
# -------------------------------

VIDEO, AUDIO, PHOTOS = "Video", "Audio", "Photos"
BEST_QUALITY = "Best available"
NO_SIGN_IN = "No sign-in"
COOKIES_FILE = "Cookies file…"
BROWSERS = [NO_SIGN_IN, "Firefox", "Chrome", "Edge", "Brave", COOKIES_FILE]
MP3_KBPS = 192

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "heic", "avif", "gif"}

# Hosts where posts can contain photos (handled by gallery-dl)
PHOTO_HOSTS = ("tiktok.com", "instagram.com", "facebook.com", "fb.watch")

# Cookies each site sets only for a signed-in account: site -> (domain, names)
SITE_LOGIN_COOKIES = {
    "YouTube": ("youtube.com", {"LOGIN_INFO", "SAPISID", "__Secure-3PAPISID"}),
    "Instagram": ("instagram.com", {"sessionid"}),
    "Facebook": ("facebook.com", {"c_user"}),
    "TikTok": ("tiktok.com", {"sessionid", "sid_tt"}),
}
SITE_NAMES = ", ".join(SITE_LOGIN_COOKIES)

# Format filter that rejects watermarked versions (TikTok marks them in
# format_note). The "?" keeps formats that have no format_note at all.
NO_WATERMARK = "[format_note!*=?watermark]"

# Hide the console window gallery-dl would otherwise flash on Windows
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# -------------------------------
# TITLE CLEANING
# -------------------------------

SOCIAL_SITES = {"facebook": "Facebook", "instagram": "Instagram", "tiktok": "TikTok"}

# A piece of text that is only a counter, e.g. "1.2M views", "3,401 reactions"
STATS_ONLY = re.compile(
    r"[\d.,]+\s*[KMB]?\+?\s*"
    r"(?:views?|reactions?|likes?|comments?|shares?|plays?|followers?|reposts?)",
    re.IGNORECASE,
)

# Instagram descriptions like: 1,234 likes, 56 comments - user on May 1, 2026: "caption"
INSTAGRAM_STATS_CAPTION = re.compile(
    r'^[^"]*\blikes?\b[^"]*:\s*"(.*)"\s*\.?$', re.DOTALL
)


def social_site(info):
    """Return "Facebook" / "Instagram" / "TikTok", or None for other sites."""

    key = (info.get("extractor_key") or info.get("extractor") or "").lower()

    return next((name for site, name in SOCIAL_SITES.items() if site in key), None)


def clean_title(info):
    """Just the title/caption: no view/like counts, page names or hashtags."""

    title = (info.get("title") or "").strip()
    site = social_site(info)
    uploader = (info.get("uploader") or info.get("channel") or "").strip()

    key = (info.get("extractor_key") or info.get("extractor") or "").lower()
    if not site and "youtube" in key:
        return clean_youtube_title(title, info, uploader)

    if not site:
        return title  # other sites: leave untouched

    # Instagram's title is just "Video by <user>"; the caption is the description
    if re.fullmatch(r"(?:video|post|reel|photo)s? by .*", title, re.IGNORECASE):
        title = (info.get("description") or "").strip()

    # "123 likes, 4 comments - user on <date>: "caption"" -> caption
    if match := INSTAGRAM_STATS_CAPTION.match(title):
        title = match.group(1)

    # Captions can be long: keep the first non-empty line only
    title = next((line.strip() for line in title.splitlines() if line.strip()), "")

    # Facebook: "1.2M views · 30K reactions | Caption | Page name"
    parts = []
    for part in re.split(r"\s*\|\s*", title):
        pieces = [p for p in re.split(r"\s*[·•]\s*", part) if p.strip()]
        pieces = [p for p in pieces if not STATS_ONLY.fullmatch(p.strip())]
        part = " · ".join(pieces).strip()

        if part and part.lower() not in {site.lower(), uploader.lower()}:
            parts.append(part)

    title = tidy_title(parts[0] if parts else "")

    if not title:
        title = f"{site} post by {uploader}" if uploader else f"{site} post"

    return title


def clean_youtube_title(title, info, uploader):
    """YouTube (especially Shorts): drop hashtags and pure counters only.

    Unlike Facebook, the parts after "|" are real title text on YouTube
    (e.g. "Song | Artist"), so they are kept.
    """

    parts = []
    for part in re.split(r"\s*\|\s*", title):
        pieces = [p for p in re.split(r"\s*[·•]\s*", part) if p.strip()]
        pieces = [p for p in pieces if not STATS_ONLY.fullmatch(p.strip())]
        part = tidy_title(" · ".join(pieces))
        if part:
            parts.append(part)

    title = " | ".join(parts)

    if not title:
        url = info.get("webpage_url") or info.get("original_url") or ""
        kind = "Short" if "/shorts/" in url else "video"
        title = f"YouTube {kind} by {uploader}" if uploader else f"YouTube {kind}"

    return title


def tidy_title(title):
    """Remove hashtags, extra spaces and stray separators; cap the length."""

    title = re.sub(r"(?:^|\s)#[^\s#]+", " ", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" -–—|·•:,")

    if len(title) > 100:
        title = title[:100].rsplit(" ", 1)[0] + "…"

    return title


class CleanTitle(PostProcessor):
    """Runs before the file name is made, so files get the cleaned title too."""

    def run(self, information):
        information["title"] = clean_title(information)
        return [], information


class QuietLogger:
    """Stops yt-dlp printing to the console. Errors still raise exceptions."""

    # yt-dlp sometimes passes extra options (e.g. only_once=True), so accept them

    def debug(self, message, **options):
        pass

    def info(self, message, **options):
        pass

    def warning(self, message, **options):
        pass

    def error(self, message, **options):
        pass


# -------------------------------
# SMALL HELPERS
# -------------------------------


def human_size(size):
    """1234567 -> "1.2 MB"."""

    if not size:
        return ""

    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            break
        size /= 1000

    return (
        f"{size:.0f} {unit}"
        if size >= 10 or unit in ("B", "KB")
        else f"{size:.1f} {unit}"
    )


def human_time(seconds):
    """75 -> "1:15"; 3725 -> "1:02:05"."""

    minutes, secs = divmod(int(seconds or 0), 60)
    hours, minutes = divmod(minutes, 60)

    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def safe_name(name, limit=80):
    """Make text usable as a Windows file/folder name."""

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" .")
    return name[:limit].strip(" .") or "download"


def is_photo_host(url):

    return any(host in url.lower() for host in PHOTO_HOSTS)


def friendly_error(error):
    """Turn yt-dlp / gallery-dl errors into short, plain advice."""

    # Remove terminal colour codes (e.g. "\x1b[0;31m") before anything else
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    message = message.strip().removeprefix("ERROR:").strip()
    lower = message.lower()

    if "requested format is not available" in lower:
        return (
            "The site didn't offer any downloadable video for this link. Try "
            'another quality, or update yt-dlp: pip install -U "yt-dlp[default]"'
        )

    if "private video" in lower:
        return (
            "This video is private. If the owner shared it with your account, "
            "choose your browser or cookies file under Sign in."
        )

    if "unsupported url" in lower:
        return (
            "This link isn't supported. Use a link to a single video or post on "
            "YouTube, TikTok, Instagram or Facebook."
        )

    # Instagram / Facebook / TikTok often only show posts to logged-in users
    login_words = (
        "login",
        "log in",
        "logged-in",
        "cookies",
        "401",
        "unauthorized",
        "authorization",
    )
    social = ("instagram", "facebook", "tiktok")
    if any(site in lower for site in social) and any(
        word in lower for word in login_words
    ):
        site = next(site for site in social if site in lower)
        site = SOCIAL_SITES[site]
        return (
            f"{site} only shows this to signed-in users. Under Sign in, choose a "
            f"browser or cookies file where you're signed in to {site}."
        )

    if "unexpected response" in lower:
        return (
            "The site sent back an unexpected answer. It may be blocking downloads "
            "from your network right now. Try again later or sign in."
        )

    if "rate-limit" in lower or "rate limit" in lower or "429" in lower:
        return "The site is limiting requests. Wait a few minutes and try again."

    if "dpapi" in lower or "app-bound" in lower or "decrypt" in lower:
        return (
            "Couldn't read this browser's login. Chrome and Edge lock their cookies "
            "on Windows: use Firefox or a cookies file instead."
        )

    if "could not copy" in lower and "cookie" in lower:
        return (
            "The browser is locking its login data. Close it completely "
            "(including background windows) and try again."
        )

    if "netscape formatted" in lower:
        return 'That cookies file is JSON. Export it again in "Netscape" format.'

    if "no such file" in lower or "errno 2" in lower:
        return "The cookies file can't be found. Choose it again."

    if "could not find" in lower and "cookies" in lower:
        return "No login data found for that browser. Is it installed?"

    if "sign in to confirm your age" in lower:
        return "This video is age-restricted. Sign in to download it."

    if "not a bot" in lower:
        return "YouTube wants to confirm you're not a bot. Sign in and try again."

    if "video unavailable" in lower:
        return "This video is unavailable (deleted, blocked, or region-locked)."

    if "no video" in lower or "no formats" in lower:
        return "There's no video in this post."

    # Otherwise: drop "[site] <id>:" / "[site][error]" prefixes and help links
    message = re.sub(r"^(?:\[[^\]]+\])+\s*(?:[\w-]+:\s*)?", "", message)
    return re.split(r"\s+(?:Use --|See\s+https?://)", message)[0] or "Unknown error."


# -------------------------------
# QUALITIES AND SIZES
# -------------------------------


def format_size(fmt, duration):
    """Bytes for one format: exact if known, else estimated from its bitrate."""

    if fmt.get("filesize"):
        return fmt["filesize"], False

    if fmt.get("filesize_approx"):
        return fmt["filesize_approx"], True

    if fmt.get("tbr") and duration:
        return int(fmt["tbr"] * 1000 / 8 * duration), True

    return 0, True


def analyse_formats(info, need_audio_in_file):
    """Qualities the video really has, best first, with estimated sizes.

    Returns a list of dicts: {label, height, size, approx}.
    """

    formats = info.get("formats") or []
    duration = info.get("duration") or 0

    # Best audio track (yt-dlp prefers M4A when sizes are equal, like we do)
    audio_only = [
        f
        for f in formats
        if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
    ]
    best_audio = max(
        audio_only,
        key=lambda f: (f.get("ext") == "m4a", f.get("abr") or 0),
        default=None,
    )
    audio_size, audio_approx = (
        format_size(best_audio, duration) if best_audio else (0, False)
    )

    by_label = {}

    for fmt in formats:
        height = fmt.get("height")

        # Skip audio-only streams and YouTube's thumbnail "storyboards"
        if not height or fmt.get("vcodec") == "none" or fmt.get("ext") == "mhtml":
            continue

        # Never offer TikTok's watermarked version
        if "watermark" in (fmt.get("format_note") or "").lower():
            continue

        has_audio = fmt.get("acodec") not in (None, "none")

        # Without ffmpeg only files that already contain sound can be used
        if need_audio_in_file and not has_audio:
            continue

        # Name by the short side, so vertical videos (Shorts) read normally
        short_side = min(height, fmt.get("width") or height)
        label = f"{short_side}p"
        if short_side >= 4320:
            label += " (8K)"
        elif short_side >= 2160:
            label += " (4K)"

        size, approx = format_size(fmt, duration)
        if not has_audio and best_audio:
            size += audio_size
            approx = approx or audio_approx

        # Prefer what yt-dlp will pick for this height: MP4, then higher fps/bitrate
        rank = (fmt.get("ext") == "mp4", fmt.get("fps") or 0, fmt.get("tbr") or 0)
        current = by_label.get(label)

        if current is None or rank > current["rank"]:
            by_label[label] = {
                "label": label,
                "height": height,
                "size": size,
                "approx": approx,
                "rank": rank,
            }

    return sorted(by_label.values(), key=lambda q: q["height"], reverse=True)


def quality_text(quality):

    size = human_size(quality["size"])

    if not size:
        return quality["label"]

    return f"{quality['label']}  ·  {'~' if quality['approx'] else ''}{size}"


# -------------------------------
# PHOTOS (gallery-dl)
# -------------------------------


def run_gallery_dl(args, **popen_options):

    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    # As an .exe there is no separate Python: the app runs gallery-dl itself
    # when started with --gallery-dl (see the bottom of this file)
    if FROZEN:
        command = [sys.executable, "--gallery-dl", *args]
    else:
        command = [sys.executable, "-m", "gallery_dl", *args]

    return command, dict(
        env=env,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
        **popen_options,
    )


def gallery_error(output):
    """Last "[site][error] ..." line from gallery-dl's output."""

    errors = [line for line in output.splitlines() if "][error]" in line]

    return errors[-1].strip() if errors else "No photos were found in this post."


def find_photos(url, cookie_args):
    """List the photos in a post without downloading them.

    Returns (photos, post) where photos is a list of image URLs and post holds
    the title/uploader/id gallery-dl found.
    """

    command, options = run_gallery_dl(
        ["-j", "-o", "videos=false", "-o", "audio=false", *cookie_args, url]
    )
    result = subprocess.run(command, capture_output=True, timeout=90, **options)

    try:
        messages = json.loads(result.stdout or "[]")
    except ValueError:
        messages = []

    photos, post = [], {}

    for message in messages:
        # gallery-dl message type 3 = "file URL": [3, url, metadata]
        if not (isinstance(message, list) and len(message) >= 3 and message[0] == 3):
            continue

        url_, meta = message[1], message[2]

        if str(meta.get("extension", "")).lower() in IMAGE_EXTS:
            photos.append(url_)
            post = post or meta

    if not photos and result.returncode:
        raise RuntimeError(gallery_error(result.stderr))

    return photos, post


def post_details(meta):
    """Title / uploader / id from gallery-dl metadata (fields differ per site)."""

    def first(*values):
        return next((str(v).strip() for v in values if v), "")

    def field(name, key):
        value = meta.get(name)
        return value.get(key) if isinstance(value, dict) else None

    site = SOCIAL_SITES.get(str(meta.get("category", "")).lower(), "")
    uploader = first(
        meta.get("username"),
        field("author", "uniqueId"),
        field("author", "nickname"),
        field("user", "username"),
        field("owner", "username"),
        meta.get("uploader"),
    )
    caption = first(
        meta.get("desc"),
        meta.get("description"),
        meta.get("caption"),
        meta.get("title"),
        meta.get("content"),
    )
    post_id = first(
        meta.get("post_shortcode"), meta.get("post_id"), meta.get("id"), "post"
    )

    title = clean_title(
        {
            "extractor_key": site or "photos",
            "title": caption,
            "uploader": uploader,
        }
    )

    return {"site": site, "title": title, "uploader": uploader, "id": post_id}


def load_thumbnail(url, max_size=(132, 132)):
    """Download a thumbnail and shrink it to fit max_size (keeps proportions)."""

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urllib.request.urlopen(request, timeout=10) as response:
        image = Image.open(io.BytesIO(response.read()))
        image.load()

    image = image.convert("RGB")
    image.thumbnail(max_size)

    return image


# -------------------------------
# APP
# -------------------------------


class VideoDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Video Downloader {APP_VERSION}")
        self.root.configure(fg_color=BG)

        self.settings = self.load_settings()

        # Installed app: save to Downloads\Video Downloader (the install folder
        # isn't a good place for files). From source: the project's downloads.
        if FROZEN:
            default_folder = os.path.join(
                os.path.expanduser("~"), "Downloads", "Video Downloader"
            )
        else:
            default_folder = os.path.join(APP_DIR, "downloads")

        folder = self.settings.get("folder") or ""
        self.download_folder = folder if os.path.isdir(folder) else default_folder
        os.makedirs(self.download_folder, exist_ok=True)

        # ffmpeg is needed to merge video+audio streams and to convert to MP3
        self.has_ffmpeg = shutil.which("ffmpeg") is not None

        # State of the current link lookup
        self.lookup = None  # result dict for the first link
        self.lookup_url = None
        self.lookup_request = 0  # increases per lookup, so stale results are ignored
        self.url_change_job = None
        self.qualities = {}  # dropdown text -> pixel height (None = best)
        self.photo_cache = {}  # url -> (photos, post details)

        # Sign-in
        self.cookie_file = self.settings.get("cookie_file")
        self.previous_sign_in = NO_SIGN_IN
        self.sign_in_request = 0

        # Download state
        self.is_downloading = False
        self.cancel_requested = False
        self.photo_process = None
        self.current_item = (0, 0)  # (index, total)

        self.fonts = {
            "title": ctk.CTkFont(FONT, 22, "bold"),
            "heading": ctk.CTkFont(FONT, 14, "bold"),
            "body": ctk.CTkFont(FONT, 13),
            "small": ctk.CTkFont(FONT, 12),
            "label": ctk.CTkFont(FONT, 12, "bold"),
            "button": ctk.CTkFont(FONT, 15, "bold"),
        }

        self.create_interface()
        self.restore_settings()

        # Let the window size itself to its content (it grows when the progress
        # area appears). CTk fixes a default size, so clear it.
        self.root.update_idletasks()
        self.root.wm_geometry("")
        self.root.minsize(640, 400)

    # -------------------------------
    # SETTINGS (remembered between sessions)
    # -------------------------------

    @staticmethod
    def load_settings():

        try:
            with open(SETTINGS_FILE, encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError):
            return {}

    def save_settings(self):

        self.settings.update(
            {
                "folder": self.download_folder,
                "sign_in": self.sign_in.get(),
                "cookie_file": self.cookie_file,
                "theme": ctk.get_appearance_mode().lower(),
            }
        )

        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
                json.dump(self.settings, file, indent=2)
        except OSError:
            pass  # not being able to remember settings isn't worth an error

    def restore_settings(self):

        sign_in = self.settings.get("sign_in", NO_SIGN_IN)

        if sign_in == COOKIES_FILE and not (
            self.cookie_file and os.path.isfile(self.cookie_file)
        ):
            sign_in = NO_SIGN_IN

        if sign_in in BROWSERS and sign_in != NO_SIGN_IN:
            self.sign_in.set(sign_in)
            self.apply_sign_in(sign_in, ask_for_file=False)

    # -------------------------------
    # INTERFACE
    # -------------------------------

    def card(self, master):

        return ctk.CTkFrame(
            master,
            fg_color=CARD,
            corner_radius=14,
            border_width=1,
            border_color=BORDER,
        )

    def ghost_button(self, master, text, command, width=0, **options):

        return ctk.CTkButton(
            master,
            text=text,
            command=command,
            width=width,
            height=32,
            corner_radius=8,
            fg_color=FIELD,
            hover_color=FIELD_HOVER,
            text_color=TEXT,
            text_color_disabled=FAINT,
            font=self.fonts["small"],
            **options,
        )

    def field_label(self, master, text):

        return ctk.CTkLabel(
            master, text=text, font=self.fonts["label"], text_color=MUTED, anchor="w"
        )

    def create_interface(self):

        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=28, pady=(22, 24))

        self.create_header(main)
        self.create_links_card(main)
        self.create_preview_card(main)
        self.create_options_card(main)
        self.create_action_area(main)

        # Keyboard shortcuts
        self.root.bind("<Control-Return>", lambda event: self.on_main_button())
        self.root.bind("<Escape>", lambda event: self.cancel_download())
        self.root.bind("<Control-l>", lambda event: self.url_box.focus_set())

        self.root.protocol("WM_DELETE_WINDOW", self.close_application)

    def create_header(self, main):

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text="Video Downloader",
            font=self.fonts["title"],
            text_color=TEXT,
        ).pack(side="left")

        self.theme_button = ctk.CTkButton(
            header,
            text="",
            width=36,
            height=32,
            corner_radius=8,
            fg_color=FIELD,
            hover_color=FIELD_HOVER,
            text_color=TEXT,
            font=self.fonts["body"],
            command=self.toggle_theme,
        )
        self.theme_button.pack(side="right")
        self.update_theme_button()

    def create_links_card(self, main):

        card = self.card(main)
        card.pack(fill="x", pady=(16, 0))

        self.url_box = ctk.CTkTextbox(
            card,
            height=88,
            corner_radius=10,
            border_width=0,
            fg_color=FIELD,
            text_color=TEXT,
            font=self.fonts["body"],
            wrap="none",
            activate_scrollbars=False,
        )
        self.url_box.pack(fill="x", padx=14, pady=(14, 10))

        # Placeholder text (CTkTextbox has none of its own)
        self.placeholder = ctk.CTkLabel(
            self.url_box,
            text="Paste a link here  (one per line for several)",
            font=self.fonts["body"],
            text_color=FAINT,
            fg_color=FIELD,
        )
        self.placeholder.place(x=12, y=8)
        self.placeholder.bind("<Button-1>", lambda event: self.url_box.focus_set())

        # Look up the link shortly after it's pasted/typed
        self.url_text = self.url_box._textbox  # the underlying tk.Text
        self.url_text.bind("<<Modified>>", self.on_urls_changed)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 12))

        self.ghost_button(row, "Paste", self.paste_link, width=70).pack(side="left")
        self.ghost_button(row, "Clear", self.clear_links, width=70).pack(
            side="left", padx=(8, 0)
        )

        ctk.CTkLabel(
            row,
            text="YouTube · TikTok · Instagram · Facebook",
            font=self.fonts["small"],
            text_color=MUTED,
        ).pack(side="right")

    def create_preview_card(self, main):

        card = self.card(main)
        card.pack(fill="x", pady=(12, 0))
        card.grid_columnconfigure(1, weight=1)

        self.thumbnail = ctk.CTkLabel(
            card,
            text="▶",
            width=132,
            height=84,
            corner_radius=10,
            fg_color=FIELD,
            text_color=FAINT,
            font=ctk.CTkFont(FONT, 22),
        )
        self.thumbnail.grid(row=0, column=0, rowspan=4, padx=14, pady=14, sticky="n")

        self.preview_title = ctk.CTkLabel(
            card,
            text="No link yet",
            font=self.fonts["heading"],
            text_color=TEXT,
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.preview_title.grid(row=0, column=1, sticky="ew", pady=(14, 0))

        self.preview_meta = ctk.CTkLabel(
            card,
            text="Paste a link above. Details, sizes and qualities appear here.",
            font=self.fonts["small"],
            text_color=MUTED,
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.preview_meta.grid(row=1, column=1, sticky="ew", pady=(2, 0))

        self.preview_badges = ctk.CTkLabel(
            card,
            text="",
            font=self.fonts["small"],
            text_color=TEXT,
            anchor="w",
        )
        self.preview_badges.grid(row=2, column=1, sticky="ew", pady=(6, 0))

        self.preview_message = ctk.CTkLabel(
            card,
            text="",
            font=self.fonts["small"],
            text_color=BAD,
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.preview_message.grid(row=3, column=1, sticky="new", pady=(2, 14))

        self.refresh_button = self.ghost_button(
            card, "↻", lambda: self.start_lookup(force=True), width=32
        )
        self.refresh_button.grid(row=0, column=2, padx=14, pady=(14, 0), sticky="ne")
        self.refresh_button.grid_remove()

        # Keep text wrapped to the space next to the thumbnail
        card.bind("<Configure>", self.on_preview_resize)

    def create_options_card(self, main):

        card = self.card(main)
        card.pack(fill="x", pady=(12, 0))
        card.grid_columnconfigure(1, weight=1)

        pad = {"padx": (16, 12)}

        # Download as
        self.field_label(card, "Download as").grid(
            row=0, column=0, sticky="w", pady=(16, 0), **pad
        )
        self.mode = ctk.CTkSegmentedButton(
            card,
            values=[VIDEO, AUDIO],
            command=self.on_mode_change,
            height=34,
            corner_radius=8,
            font=self.fonts["small"],
            fg_color=FIELD,
            unselected_color=FIELD,
            unselected_hover_color=FIELD_HOVER,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            text_color=TEXT,
        )
        self.mode.set(VIDEO)
        self.mode.grid(row=0, column=1, sticky="w", pady=(16, 0), padx=(0, 16))

        # Quality (or what will be saved, for audio/photos)
        self.field_label(card, "Quality").grid(
            row=1, column=0, sticky="w", pady=(12, 0), **pad
        )
        self.quality = ctk.CTkOptionMenu(
            card,
            values=[BEST_QUALITY],
            width=250,
            height=34,
            corner_radius=8,
            font=self.fonts["small"],
            dropdown_font=self.fonts["small"],
            fg_color=FIELD,
            button_color=FIELD,
            button_hover_color=FIELD_HOVER,
            text_color=TEXT,
            text_color_disabled=MUTED,
            dynamic_resizing=False,
        )
        self.quality.grid(row=1, column=1, sticky="w", pady=(12, 0), padx=(0, 16))

        # Divider
        ctk.CTkFrame(card, height=1, corner_radius=0, fg_color=BORDER).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=16
        )

        # Save to
        self.field_label(card, "Save to").grid(row=3, column=0, sticky="w", **pad)

        folder_row = ctk.CTkFrame(card, fg_color="transparent")
        folder_row.grid(row=3, column=1, sticky="ew", padx=(0, 16))

        self.folder_label = ctk.CTkLabel(
            folder_row,
            text=self.short_path(self.download_folder),
            font=self.fonts["small"],
            text_color=TEXT,
            anchor="w",
        )
        self.folder_label.pack(side="left")

        self.ghost_button(folder_row, "Change", self.choose_folder, width=70).pack(
            side="right"
        )

        # Sign in
        self.field_label(card, "Sign in").grid(
            row=4, column=0, sticky="w", pady=(10, 0), **pad
        )
        self.sign_in = ctk.CTkOptionMenu(
            card,
            values=BROWSERS,
            command=self.on_sign_in_change,
            width=170,
            height=34,
            corner_radius=8,
            font=self.fonts["small"],
            dropdown_font=self.fonts["small"],
            fg_color=FIELD,
            button_color=FIELD,
            button_hover_color=FIELD_HOVER,
            text_color=TEXT,
            dynamic_resizing=False,
        )
        self.sign_in.set(NO_SIGN_IN)
        self.sign_in.grid(row=4, column=1, sticky="w", pady=(10, 0), padx=(0, 16))

        hint = (
            "Only needed for private, age-restricted or login-only posts."
            if self.has_ffmpeg
            else "ffmpeg not found: MP3 is unavailable and video quality is limited."
        )
        self.sign_in_note = ctk.CTkLabel(
            card,
            text=hint,
            font=self.fonts["small"],
            text_color=MUTED if self.has_ffmpeg else WARN,
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.sign_in_note.grid(row=5, column=1, sticky="ew", pady=(6, 16), padx=(0, 16))
        self.default_note = (hint, MUTED if self.has_ffmpeg else WARN)

    def create_action_area(self, main):

        self.main_button = ctk.CTkButton(
            main,
            text="Download",
            command=self.on_main_button,
            height=46,
            corner_radius=12,
            font=self.fonts["button"],
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="white",
            text_color_disabled=("#fbd5d6", "#8a3b3e"),
        )
        self.main_button.pack(fill="x", pady=(16, 0))

        # Progress / result (hidden until a download starts)
        self.status_area = ctk.CTkFrame(main, fg_color="transparent")

        self.progress = ctk.CTkProgressBar(
            self.status_area,
            height=6,
            corner_radius=3,
            fg_color=FIELD,
            progress_color=ACCENT,
        )
        self.progress.set(0)
        self.progress.pack(fill="x")

        row = ctk.CTkFrame(self.status_area, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))

        self.status_label = ctk.CTkLabel(
            row,
            text="",
            font=self.fonts["small"],
            text_color=MUTED,
            anchor="w",
            justify="left",
            wraplength=460,
        )
        self.status_label.pack(side="left", fill="x", expand=True)

        self.open_folder_button = self.ghost_button(
            row, "Open folder", self.open_folder, width=100
        )

        self.speed_label = ctk.CTkLabel(
            row, text="", font=self.fonts["small"], text_color=MUTED
        )
        self.speed_label.pack(side="right")

        self.update_main_button()

    # -------------------------------
    # SMALL UI HELPERS
    # -------------------------------

    @staticmethod
    def short_path(path, max_length=46):

        path = os.path.normpath(path)

        if len(path) <= max_length:
            return path

        parts = path.split(os.sep)
        short = os.path.join("…", *parts[-2:])

        return short if len(short) <= max_length else "…" + path[-(max_length - 1) :]

    def on_preview_resize(self, event):

        width = max(event.width - 132 - 28 - 60, 200)

        for label in (self.preview_title, self.preview_meta, self.preview_message):
            label.configure(wraplength=width)

    def toggle_theme(self):

        dark = ctk.get_appearance_mode().lower() == "dark"
        ctk.set_appearance_mode("light" if dark else "dark")
        self.update_theme_button()
        self.save_settings()

    def update_theme_button(self):

        dark = ctk.get_appearance_mode().lower() == "dark"
        self.theme_button.configure(text="☀" if dark else "☾")

    def show_note(self, text, color):

        self.sign_in_note.configure(text=text, text_color=color)

    def get_urls(self):

        text = self.url_box.get("1.0", "end")

        return [url.strip() for url in text.splitlines() if url.strip()]

    def paste_link(self):

        try:
            text = self.root.clipboard_get().strip()
        except Exception:
            return

        if not text:
            return

        current = self.url_box.get("1.0", "end").strip()
        self.url_box.insert("end", ("\n" if current else "") + text)
        self.url_box.focus_set()

    def clear_links(self):

        self.url_box.delete("1.0", "end")
        self.url_box.focus_set()

    def choose_folder(self):

        folder = filedialog.askdirectory(initialdir=self.download_folder)

        if folder:
            self.download_folder = folder
            self.folder_label.configure(text=self.short_path(folder))
            self.save_settings()

    def open_folder(self):

        if os.path.isdir(self.download_folder):
            os.startfile(self.download_folder)

    def update_main_button(self):

        if self.is_downloading:
            self.main_button.configure(
                text="Cancel  (Esc)",
                state="normal",
                fg_color=FIELD,
                hover_color=FIELD_HOVER,
                text_color=TEXT,
            )
            return

        urls = self.get_urls()
        mode = self.mode.get()
        count = len(urls)

        if mode == PHOTOS:
            text = "Download photos"
        elif count > 1:
            text = f"Download {count} links"
        else:
            text = "Download"

        self.main_button.configure(
            text=text,
            state="normal" if urls else "disabled",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="white",
        )

    # -------------------------------
    # LINK LOOKUP (details, qualities, photos, thumbnail)
    # -------------------------------

    def on_urls_changed(self, event=None):

        # Reset the flag so the next edit fires <<Modified>> again
        self.url_text.edit_modified(False)

        has_text = bool(self.url_box.get("1.0", "end").strip())
        if has_text:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=12, y=8)

        self.update_main_button()

        # Wait until typing/pasting settles before looking anything up
        if self.url_change_job:
            self.root.after_cancel(self.url_change_job)

        self.url_change_job = self.root.after(600, self.on_urls_settled)

    def on_urls_settled(self):

        self.url_change_job = None

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
        }

        sign_in = self.sign_in.get()

        if sign_in == COOKIES_FILE and self.cookie_file:
            options["cookiefile"] = self.cookie_file
        elif sign_in not in (NO_SIGN_IN, COOKIES_FILE):
            options["cookiesfrombrowser"] = (sign_in.lower(),)

        return options

    def gallery_cookie_args(self):

        sign_in = self.sign_in.get()

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

        # Read widget values here: Tk must only be touched from the main thread
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
        }

        # 1. Video / audio via yt-dlp
        try:
            with yt_dlp.YoutubeDL(ydl_options) as downloader:
                info = downloader.extract_info(url, download=False)

            # Posts with several items come back as a list: use the first video
            entries = list(info.get("entries") or [])
            video = info if info.get("formats") or not entries else entries[0]

            site = info.get("extractor_key") or info.get("extractor") or ""
            result["site"] = {"Youtube": "YouTube", "TikTok": "TikTok"}.get(site, site)
            result["title"] = clean_title(info) or clean_title(video)
            result["uploader"] = (
                video.get("uploader") or video.get("channel") or info.get("uploader")
            )
            result["duration"] = video.get("duration") or 0
            result["qualities"] = analyse_formats(video, not self.has_ffmpeg)
            result["has_video"] = bool(result["qualities"])
            result["has_audio"] = any(
                f.get("acodec") not in (None, "none")
                for f in video.get("formats") or []
            )
            result["videos"] = len(entries) or (1 if result["has_video"] else 0)
            result["thumbnail_url"] = video.get("thumbnail") or info.get("thumbnail")

        except Exception as error:
            result["error"] = friendly_error(error)

        # 2. Photos via gallery-dl (TikTok slideshows, Instagram, Facebook)
        if is_photo_host(url):
            try:
                photos, meta = find_photos(url, cookie_args)
                details = post_details(meta) if meta else {}
                self.photo_cache[url] = (photos, details)

                result["photos"] = len(photos)

                if photos:
                    result["error"] = None
                    result["title"] = result["title"] or details.get("title", "")
                    result["site"] = result["site"] or details.get("site", "")
                    result["uploader"] = result["uploader"] or details.get(
                        "uploader", ""
                    )
                    result["thumbnail_url"] = result["thumbnail_url"] or photos[0]

            except Exception as error:
                # Only matters if yt-dlp found nothing either
                if not (result["has_video"] or result["has_audio"]):
                    result["error"] = result["error"] or friendly_error(error)

        # 3. Thumbnail picture
        if result["thumbnail_url"] and not result["error"]:
            try:
                result["thumbnail"] = load_thumbnail(result["thumbnail_url"])
            except Exception:
                pass  # no thumbnail is fine

        self.root.after(0, lambda: self.show_lookup(result, request))

    def show_empty_preview(self):

        self.lookup = None
        self.set_thumbnail(None, "▶")
        self.preview_title.configure(text="No link yet")
        self.preview_meta.configure(
            text="Paste a link above. Details, sizes and qualities appear here."
        )
        self.preview_badges.configure(text="")
        self.preview_message.configure(text="")
        self.refresh_button.grid_remove()
        self.set_modes([VIDEO, AUDIO])
        self.set_quality_choices([])

    def show_loading_preview(self):

        self.set_thumbnail(None, "…")
        self.preview_title.configure(text="Looking up the link…")
        self.preview_meta.configure(text="Getting title, qualities and sizes")
        self.preview_badges.configure(text="")
        self.preview_message.configure(text="")
        self.refresh_button.grid_remove()
        self.quality.configure(values=["Checking…"], state="disabled")
        self.quality.set("Checking…")

    def set_thumbnail(self, image, placeholder="▶"):

        if image is None:
            self.thumbnail.configure(
                image=None, text=placeholder, width=132, height=84, fg_color=FIELD
            )
            self.thumbnail_image = None
            return

        self.thumbnail_image = ctk.CTkImage(
            light_image=image, dark_image=image, size=image.size
        )
        self.thumbnail.configure(
            image=self.thumbnail_image,
            text="",
            width=image.width,
            height=image.height,
            fg_color="transparent",
        )

    def show_lookup(self, result, request):

        # Ignore answers for a link that has since been changed
        if request != self.lookup_request:
            return

        self.lookup = result
        self.refresh_button.grid()

        if result["error"]:
            self.set_thumbnail(None, "!")
            self.preview_title.configure(text="Couldn't open this link")
            self.preview_meta.configure(text=result["url"])
            self.preview_badges.configure(text="")
            self.preview_message.configure(text=result["error"], text_color=BAD)
            self.set_modes([VIDEO, AUDIO])
            self.set_quality_choices([])
            return

        self.set_thumbnail(result["thumbnail"])
        self.preview_title.configure(text=result["title"] or "Untitled")

        meta = [result["site"], result["uploader"]]
        if result["duration"]:
            meta.append(human_time(result["duration"]))
        self.preview_meta.configure(text="  ·  ".join(part for part in meta if part))

        badges = []
        if result["videos"] > 1:
            badges.append(f"🎬 {result['videos']} videos")
        elif result["has_video"]:
            badges.append("🎬 Video")
        if result["has_audio"] and not result["has_video"]:
            badges.append("🎵 Audio")
        if result["photos"]:
            plural = "s" if result["photos"] > 1 else ""
            badges.append(f"🖼 {result['photos']} photo{plural}")
        self.preview_badges.configure(text="     ".join(badges))
        self.preview_message.configure(text="")

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
        self.mode.configure(values=modes)
        self.mode.set(current if current in modes else modes[0])
        self.on_mode_change(self.mode.get())

    def on_mode_change(self, value=None):

        self.update_quality_menu()
        self.update_main_button()

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
            self.quality.configure(values=[text], state="disabled")
            self.quality.set(text)
            return

        if mode == PHOTOS:
            count = lookup.get("photos") or 0
            text = f"{count} photo{'s' if count != 1 else ''} · original quality"
            self.quality.configure(values=[text], state="disabled")
            self.quality.set(text)
            return

        qualities = getattr(self, "quality_list", [])

        if not qualities:
            self.qualities = {BEST_QUALITY: None}
            self.quality.configure(values=[BEST_QUALITY], state="normal")
            self.quality.set(BEST_QUALITY)
            return

        # Keep the chosen height if this video has it too
        previous_height = self.qualities.get(self.quality.get())

        self.qualities = {quality_text(q): q["height"] for q in qualities}
        self.quality.configure(values=list(self.qualities), state="normal")

        choice = next(
            (text for text, h in self.qualities.items() if h == previous_height),
            next(iter(self.qualities)),
        )
        self.quality.set(choice)

    # -------------------------------
    # SIGN IN
    # -------------------------------

    def on_sign_in_change(self, value):

        self.apply_sign_in(value, ask_for_file=True)

    def apply_sign_in(self, value, ask_for_file):

        if value == COOKIES_FILE and (ask_for_file or not self.cookie_file):
            path = filedialog.askopenfilename(
                title="Choose your cookies.txt file",
                filetypes=[("Cookies file", "*.txt"), ("All files", "*.*")],
            )

            if not path:
                # Cancelled: go back to whatever was selected before
                self.sign_in.set(self.previous_sign_in)
                return

            self.cookie_file = path

        self.previous_sign_in = value
        self.sign_in_request += 1
        self.save_settings()

        if value == NO_SIGN_IN:
            self.show_note(*self.default_note)
        else:
            source = self.sign_in_source(value)
            self.show_note(f"Checking which sites {source} is signed in to…", MUTED)

            threading.Thread(
                target=self.check_sign_in,
                args=(value, self.cookie_file, self.sign_in_request),
                daemon=True,
            ).start()

        # Sign-in can change what a link offers (or unlock a private one)
        if self.get_urls():
            self.start_lookup(force=True)

    def sign_in_source(self, value, cookie_file=None):

        if value == COOKIES_FILE:
            return os.path.basename(cookie_file or self.cookie_file or "cookies file")

        return value

    def check_sign_in(self, value, cookie_file, request):

        # Reads saved cookies on this PC only (nothing is sent anywhere)
        source = self.sign_in_source(value, cookie_file)

        try:
            from yt_dlp.cookies import (
                YoutubeDLCookieJar,
                extract_cookies_from_browser,
            )

            if value == COOKIES_FILE:
                jar = YoutubeDLCookieJar(cookie_file)
                jar.load()
            else:
                jar = extract_cookies_from_browser(value.lower(), logger=QuietLogger())

            cookies = [cookie for cookie in jar if not cookie.is_expired()]

            signed_in = [
                site
                for site, (domain, names) in SITE_LOGIN_COOKIES.items()
                if any(
                    cookie.domain.endswith(domain) and cookie.name in names
                    for cookie in cookies
                )
            ]

            if signed_in:
                text = f"✓  Signed in via {source} to {', '.join(signed_in)}."
                color = GOOD
            elif value == COOKIES_FILE:
                text = (
                    f"✕  {source} has no valid login for {SITE_NAMES} (or it has "
                    "expired). Export the cookies again while signed in."
                )
                color = BAD
            else:
                text = (
                    f"✕  {value} isn't signed in to {SITE_NAMES}. Sign in there, "
                    f"close {value}, then choose it again."
                )
                color = BAD

        except Exception as error:
            text, color = f"✕  {friendly_error(error)}", BAD

        def show():
            # Ignore if the user has picked something else since
            if request == self.sign_in_request:
                self.show_note(text, color)

        self.root.after(0, show)

    # -------------------------------
    # DOWNLOAD
    # -------------------------------

    def on_main_button(self):

        if self.is_downloading:
            self.cancel_download()
        else:
            self.start_download()

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

    def start_download(self):

        if self.is_downloading:
            return

        urls = self.get_urls()

        if not urls:
            self.url_box.focus_set()
            return

        mode = self.mode.get()

        if mode == AUDIO and not self.has_ffmpeg:
            self.show_status(
                "MP3 needs ffmpeg, which wasn't found. Install it "
                "(winget install Gyan.FFmpeg) and restart the app.",
                BAD,
            )
            return

        self.is_downloading = True
        self.cancel_requested = False

        self.update_main_button()
        self.status_area.pack(fill="x", pady=(14, 0))
        self.open_folder_button.pack_forget()
        self.progress.set(0)
        self.show_status("Starting…", MUTED)

        # Read widget values here: Tk must only be touched from the main thread
        if mode == PHOTOS:
            job, options = self.download_all_photos, self.gallery_cookie_args()
        else:
            job, options = self.download_all_videos, self.get_download_options()

        threading.Thread(target=job, args=(urls, options), daemon=True).start()

    def cancel_download(self):

        if not self.is_downloading or self.cancel_requested:
            return

        self.cancel_requested = True
        self.show_status("Cancelling…", MUTED)

        if self.photo_process and self.photo_process.poll() is None:
            self.photo_process.terminate()

    def download_all_videos(self, urls, options):

        saved, errors = 0, []

        for index, url in enumerate(urls):
            if self.cancel_requested:
                break

            self.current_item = (index + 1, len(urls))

            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    # Clean social-media titles before the file is named
                    downloader.add_post_processor(CleanTitle(), when="pre_process")
                    downloader.download([url])

                saved += 1

            except Exception as error:
                if self.cancel_requested:
                    break
                errors.append(friendly_error(error))

        self.root.after(0, lambda: self.download_finished(saved, errors, "file"))

    def download_all_photos(self, urls, cookie_args):

        saved, errors = 0, []

        for index, url in enumerate(urls):
            if self.cancel_requested:
                break

            self.current_item = (index + 1, len(urls))

            try:
                saved += self.download_photos(url, cookie_args)
            except Exception as error:
                if self.cancel_requested:
                    break
                errors.append(friendly_error(error))

        self.root.after(0, lambda: self.download_finished(saved, errors, "photo"))

    def download_photos(self, url, cookie_args):

        # Reuse what the preview found; otherwise look the post up first
        if url not in self.photo_cache:
            self.ui(self.show_status, "Finding photos…", MUTED)
            photos, meta = find_photos(url, cookie_args)
            self.photo_cache[url] = (photos, post_details(meta) if meta else {})

        photos, details = self.photo_cache[url]

        if not photos:
            raise RuntimeError("There are no photos in this post.")

        # Each post gets its own folder: "<title> [<id>]"
        folder_name = safe_name(
            f"{details.get('title') or 'Photos'} [{details.get('id', 'post')}]"
        )
        target = os.path.join(self.download_folder, folder_name)

        command, options = run_gallery_dl(
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

        self.photo_process = subprocess.Popen(command, **options)
        total, done, output = len(photos), 0, []

        # gallery-dl prints one line per saved file ("# path" if it already existed)
        for line in self.photo_process.stdout:
            line = line.strip()
            output.append(line)

            if line.startswith(("# ", target)) or os.path.isabs(line):
                done += 1
                self.ui(self.update_photo_progress, done, total)

        self.photo_process.wait()

        if self.cancel_requested:
            raise DownloadCancelled()

        if self.photo_process.returncode and not done:
            raise RuntimeError(gallery_error("\n".join(output)))

        return done

    def ui(self, function, *args):
        """Run a function on the main (Tk) thread."""

        self.root.after(0, lambda: function(*args))

    # -------------------------------
    # PROGRESS
    # -------------------------------

    def item_prefix(self):

        index, total = self.current_item
        return f"{index} of {total}  ·  " if total > 1 else ""

    def progress_hook(self, data):

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

        if not self.is_downloading or self.cancel_requested:
            return

        self.progress.set(fraction)
        self.show_status(text, MUTED)
        self.speed_label.configure(text=details)

    def update_photo_progress(self, done, total):

        fraction = done / total if total else 0
        text = f"{self.item_prefix()}Saving photos {done} of {total}"
        self.update_progress(min(fraction, 1), text, "")

    def show_status(self, text, color):

        self.status_area.pack(fill="x", pady=(14, 0))
        self.status_label.configure(text=text, text_color=color)

    def download_finished(self, saved, errors, kind):

        cancelled = self.cancel_requested

        self.is_downloading = False
        self.cancel_requested = False
        self.photo_process = None
        self.update_main_button()
        self.speed_label.configure(text="")

        files = f"{saved} {kind}{'s' if saved != 1 else ''}"

        if cancelled:
            text = "Download cancelled." + (f" {files} saved." if saved else "")
            self.show_status(text, MUTED)
        elif errors:
            self.progress.set(0)
            reasons = "\n".join(f"•  {reason}" for reason in dict.fromkeys(errors))
            head = f"{files} saved, {len(errors)} failed:\n" if saved else ""
            self.show_status(head + reasons, BAD)
        else:
            self.progress.set(1)
            self.show_status(f"✓  Done. {files} saved.", GOOD)

        if saved:
            self.open_folder_button.pack(side="right")

    def close_application(self):

        if self.is_downloading:
            if not messagebox.askyesno(
                "Download in progress", "A download is running. Cancel it and quit?"
            ):
                return

            self.cancel_download()

        self.save_settings()
        self.root.destroy()


# -------------------------------
# MAIN PROGRAM
# -------------------------------


def run_gallery_dl_cli():
    """VideoDownloader.exe --gallery-dl <args>: behave like the gallery-dl tool."""

    import gallery_dl

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")

    sys.argv = ["gallery-dl", *sys.argv[2:]]
    sys.exit(gallery_dl.main())


def set_window_icon(root):

    if os.path.isfile(ICON_FILE):
        root.iconbitmap(ICON_FILE)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--gallery-dl":
        run_gallery_dl_cli()

    settings = VideoDownloader.load_settings()
    ctk.set_appearance_mode(settings.get("theme", "system"))

    root = ctk.CTk()
    root.title(f"Video Downloader {APP_VERSION}")

    # CustomTkinter sets its own icon shortly after start, so set ours after it
    root.after(300, set_window_icon, root)

    app = VideoDownloader(root)
    root.mainloop()
