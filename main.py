import ctypes
import hashlib
import io
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler
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

APP_VERSION = "1.0.1"

# Updates come from the latest release of this GitHub repo. The release must
# have a "VideoDownloader-Setup-<version>.exe" file attached (build.ps1 makes it).
GITHUB_REPO = "gayashvihangana/video-downloader"
UPDATE_ASSET = re.compile(r"^VideoDownloader-Setup-[\w.-]+\.exe$", re.IGNORECASE)

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

# Error-handling options (shown under "More options")
RETRY_CHOICES = {"Don't retry": 0, "Retry 3 times": 3, "Retry 10 times": 10}
DEFAULT_RETRY = "Retry 3 times"
SKIP_FAILED, STOP_ON_ERROR = "Skip it, keep going", "Stop everything"

# -------------------------------
# ERROR LOG
# -------------------------------

# %LOCALAPPDATA%\VideoDownloader\error.log - full details for fixing bugs,
# while the window only shows short, plain messages
LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "VideoDownloader"
)
LOG_FILE = os.path.join(LOG_DIR, "error.log")

log = logging.getLogger("video_downloader")
log.addHandler(logging.NullHandler())  # silent until setup_logging() runs
log.propagate = False


def setup_logging():
    """Write warnings and errors to the log file (kept small: 3 x 500 KB)."""

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8"
        )
    except OSError:
        return  # can't write a log: the app still works

    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


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


LINK = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)


def check_links(lines):
    """Split pasted lines into usable links and problems.

    Returns (links, problems). Duplicate links are dropped (kept once).
    """

    links, problems, seen = [], [], set()

    for number, line in enumerate(lines, start=1):
        if not LINK.match(line):
            short = line if len(line) <= 40 else line[:40] + "…"
            problems.append(f'Line {number} isn\'t a link: "{short}"')
        elif line not in seen:
            seen.add(line)
            links.append(line)

    return links, problems


def check_folder(folder, needed_bytes=0):
    """Why we can't save to `folder`, or None if it's fine."""

    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return "Can't create the save folder. Choose another one under Save to."

    try:
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError:
        return "Can't save in this folder (no permission). Choose another one."

    if needed_bytes:
        free = shutil.disk_usage(folder).free

        # 5% extra: merging video + audio briefly needs a little more room
        if free < needed_bytes * 1.05:
            return (
                f"Not enough disk space: this needs about {human_size(needed_bytes)}"
                f" but only {human_size(free)} is free."
            )

    return None


def retry_options(retries):
    """yt-dlp settings for the "If a download fails" option."""

    return {
        "retries": retries,
        "fragment_retries": retries,
        "extractor_retries": min(retries, 3),
        "file_access_retries": min(retries, 3),
        "socket_timeout": 30,  # don't wait forever on a dead connection
    }


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
            "choose your browser or cookies file in ⚙ Settings → Sign in."
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
            f"{site} only shows this to signed-in users. In ⚙ Settings → Sign in, "
            f"choose a browser or cookies file where you're signed in to {site}."
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

    # \b: "errno 2" must not match "errno 28" (disk full)
    if "no such file" in lower or re.search(r"\berrno 2\b", lower):
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

    offline_words = (
        "getaddrinfo",
        "name resolution",
        "failed to resolve",
        "no address associated",
        "network is unreachable",
        "connection refused",
        "connection reset",
        "timed out",
        "urlopen error",
        "remote end closed",
    )
    if any(word in lower for word in offline_words):
        return "Can't reach the site. Check your internet connection and try again."

    if "no space left" in lower or "errno 28" in lower or "disk full" in lower:
        return "Your disk is full. Free some space or choose another folder."

    blocked_words = ("permission denied", "errno 13", "access is denied")
    if any(word in lower for word in blocked_words):
        return (
            "Windows blocked saving the file (no permission, or the file is open "
            "in another program). Close it or choose another folder."
        )

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


def summarize_video(info, need_audio_in_file):
    """What the preview needs from yt-dlp's info about a link."""

    # Posts with several items come back as a list: use the first video
    entries = list(info.get("entries") or [])
    video = info if info.get("formats") or not entries else entries[0]

    site = info.get("extractor_key") or info.get("extractor") or ""
    qualities = analyse_formats(video, need_audio_in_file)

    return {
        "site": {"Youtube": "YouTube", "TikTok": "TikTok"}.get(site, site),
        "title": clean_title(info) or clean_title(video),
        "uploader": (
            video.get("uploader") or video.get("channel") or info.get("uploader") or ""
        ),
        "duration": video.get("duration") or 0,
        "qualities": qualities,
        "has_video": bool(qualities),
        "has_audio": any(
            f.get("acodec") not in (None, "none") for f in video.get("formats") or []
        ),
        "videos": len(entries) or (1 if qualities else 0),
        "thumbnail_url": video.get("thumbnail") or info.get("thumbnail"),
    }


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


def set_process_paused(process, paused):
    """Freeze / unfreeze a running process (used to pause gallery-dl)."""

    if not process or process.poll() is not None or sys.platform != "win32":
        return

    process_suspend_resume = 0x0800
    kernel32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
    handle = kernel32.OpenProcess(process_suspend_resume, False, process.pid)

    if not handle:
        return

    try:
        if paused:
            ntdll.NtSuspendProcess(handle)
        else:
            ntdll.NtResumeProcess(handle)
    finally:
        kernel32.CloseHandle(handle)


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


# -------------------------------
# UPDATES (GitHub Releases)
# -------------------------------


def parse_version(text):
    """ "v1.10.2" -> (1, 10, 2), so versions compare correctly."""

    return tuple(int(number) for number in re.findall(r"\d+", text or "")[:3])


def fetch_latest_release():
    """Newest published release and its Setup.exe, or None if it has none."""

    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "VideoDownloader-updater",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None  # no release published yet
        raise

    asset = next(
        (a for a in data.get("assets") or [] if UPDATE_ASSET.match(a.get("name", ""))),
        None,
    )

    if not asset:
        return None

    return {
        "version": (data.get("tag_name") or "").lstrip("vV"),
        "name": asset["name"],
        "url": asset["browser_download_url"],
        "size": asset.get("size") or 0,
        # GitHub publishes a SHA-256 checksum for each release file
        "digest": (asset.get("digest") or "").lower(),
    }


def download_update(release, on_progress):
    """Download the new Setup.exe to the temp folder and check it's intact."""

    target = os.path.join(tempfile.gettempdir(), safe_name(release["name"]))
    partial = target + ".part"
    checksum = hashlib.sha256()

    request = urllib.request.Request(
        release["url"], headers={"User-Agent": "VideoDownloader-updater"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        total = int(response.headers.get("Content-Length") or release["size"] or 0)
        done = 0

        with open(partial, "wb") as file:
            while chunk := response.read(256 * 1024):
                file.write(chunk)
                checksum.update(chunk)
                done += len(chunk)
                on_progress(done, total)

    expected = release["digest"].removeprefix("sha256:")
    damaged = (total and done != total) or (
        expected and checksum.hexdigest() != expected
    )

    if damaged:
        os.remove(partial)
        raise RuntimeError("The update was damaged while downloading. Try again.")

    os.replace(partial, target)

    return target


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
        self.last_fraction = 0

        # Pause: the download thread waits while this event is cleared
        self.paused = False
        self.resume_event = threading.Event()
        self.resume_event.set()

        # Links that failed in the last run (for "Retry failed")
        self.failed_links = []

        # Work handed from background threads to the main thread (see ui())
        self.ui_queue = queue.SimpleQueue()
        self.root.after(50, self.process_ui_queue)

        # Unexpected errors in button clicks etc.: log them and say so,
        # instead of failing silently
        self.root.report_callback_exception = self.on_unexpected_error

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
                "retry": self.retry_menu.get(),
                "on_error": self.on_error_menu.get(),
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
        self.create_update_banner(main)

        # Two pages in one window: the main screen and Settings (⚙)
        pages = ctk.CTkFrame(main, fg_color="transparent")
        pages.pack(fill="x")

        self.home_page = ctk.CTkFrame(pages, fg_color="transparent")
        self.home_page.pack(fill="x")
        self.settings_page = self.create_settings_page(pages)

        self.create_links_card(self.home_page)
        self.create_preview_card(self.home_page)
        self.create_options_card(self.home_page)
        self.create_action_area(self.home_page)

        # Keyboard shortcuts
        self.root.bind("<Control-Return>", lambda event: self.on_main_button())
        self.root.bind("<Escape>", lambda event: self.cancel_download())
        self.root.bind("<Control-p>", lambda event: self.toggle_pause())
        self.root.bind("<Control-l>", lambda event: self.url_box.focus_set())

        self.root.protocol("WM_DELETE_WINDOW", self.close_application)

    def create_header(self, main):

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x")
        self.header = header

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

        self.settings_button = ctk.CTkButton(
            header,
            text="⚙",
            width=36,
            height=32,
            corner_radius=8,
            fg_color=FIELD,
            hover_color=FIELD_HOVER,
            text_color=TEXT,
            font=self.fonts["body"],
            command=self.toggle_settings,
        )
        self.settings_button.pack(side="right", padx=(0, 6))

        # Shows the version; click to check for updates
        self.version_button = ctk.CTkButton(
            header,
            text=f"v{APP_VERSION}",
            width=0,
            height=32,
            corner_radius=8,
            fg_color="transparent",
            hover_color=FIELD,
            text_color=MUTED,
            font=self.fonts["small"],
            command=lambda: self.check_for_updates(manual=True),
        )
        self.version_button.pack(side="right", padx=(0, 6))

    def create_update_banner(self, main):

        # Hidden until an update is found (or the user checks by hand)
        self.update_banner = ctk.CTkFrame(
            main,
            fg_color=("#fdeced", "#2a1719"),
            corner_radius=12,
            border_width=1,
            border_color=("#f6c4c6", "#4a2427"),
        )

        self.update_label = ctk.CTkLabel(
            self.update_banner,
            text="",
            font=self.fonts["small"],
            text_color=TEXT,
            anchor="w",
            justify="left",
            wraplength=400,
        )
        self.update_label.pack(side="left", fill="x", expand=True, padx=14, pady=10)

        self.later_button = self.ghost_button(
            self.update_banner, "Later", self.hide_update_banner, width=64
        )
        self.later_button.pack(side="right", padx=(0, 10), pady=10)

        self.update_button = ctk.CTkButton(
            self.update_banner,
            text="Update now",
            command=self.start_update,
            width=0,
            height=32,
            corner_radius=8,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="white",
            font=self.fonts["label"],
        )

        self.update_release = None

        # The installed app checks quietly a moment after it opens
        if FROZEN:
            self.root.after(2500, self.check_for_updates)

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
            row=1, column=0, sticky="w", pady=(12, 16), **pad
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
            command=self.on_quality_change,
        )
        self.quality.grid(row=1, column=1, sticky="w", pady=(12, 16), padx=(0, 16))

        # Height the user picked; reused for the next link if it has it
        self.preferred_height = None

    def create_settings_page(self, parent):
        """Less-used options live on their own page (opened with ⚙), so the
        main screen stays short and simple."""

        page = ctk.CTkFrame(parent, fg_color="transparent")

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", pady=(16, 0))
        self.ghost_button(top, "←  Back", self.show_home, width=84).pack(side="left")
        ctk.CTkLabel(
            top, text="Settings", font=self.fonts["heading"], text_color=TEXT
        ).pack(side="left", padx=12)

        def section(title):
            ctk.CTkLabel(
                page, text=title, font=self.fonts["label"], text_color=MUTED
            ).pack(anchor="w", pady=(18, 6), padx=4)
            card = self.card(page)
            card.pack(fill="x")
            card.grid_columnconfigure(1, weight=1)
            return card

        def option_menu(card, values, current, command, width=200):
            menu = ctk.CTkOptionMenu(
                card,
                values=values,
                command=command,
                width=width,
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
            menu.set(current)
            return menu

        def note(card, text, row):
            label = ctk.CTkLabel(
                card,
                text=text,
                font=self.fonts["small"],
                text_color=MUTED,
                anchor="w",
                justify="left",
                wraplength=440,
            )
            label.grid(row=row, column=1, sticky="ew", padx=(0, 16), pady=(4, 0))
            return label

        pad = {"padx": (16, 12)}

        # --- Downloads ---
        card = section("DOWNLOADS")

        self.field_label(card, "Save to").grid(
            row=0, column=0, sticky="w", pady=(16, 0), **pad
        )
        folder_row = ctk.CTkFrame(card, fg_color="transparent")
        folder_row.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(16, 0))

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

        self.field_label(card, "Sign in").grid(
            row=1, column=0, sticky="w", pady=(12, 0), **pad
        )
        self.sign_in = option_menu(
            card, BROWSERS, NO_SIGN_IN, self.on_sign_in_change, width=170
        )
        self.sign_in.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(12, 0))

        hint = (
            "Only needed for private, age-restricted or login-only posts."
            if self.has_ffmpeg
            else "ffmpeg not found: MP3 is unavailable and video quality is limited."
        )
        self.default_note = (hint, MUTED if self.has_ffmpeg else WARN)
        self.sign_in_note = note(card, hint, row=2)
        self.sign_in_note.configure(text_color=self.default_note[1])
        self.sign_in_note.grid_configure(pady=(4, 16))

        # --- When something goes wrong ---
        card = section("WHEN SOMETHING GOES WRONG")

        retry = self.settings.get("retry", DEFAULT_RETRY)
        self.field_label(card, "If it fails").grid(
            row=0, column=0, sticky="w", pady=(16, 0), **pad
        )
        self.retry_menu = option_menu(
            card,
            list(RETRY_CHOICES),
            retry if retry in RETRY_CHOICES else DEFAULT_RETRY,
            lambda value: self.save_settings(),
        )
        self.retry_menu.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=(16, 0))
        note(card, "Retries help with slow or unstable internet.", row=1)

        on_error = self.settings.get("on_error", SKIP_FAILED)
        self.field_label(card, "Several links").grid(
            row=2, column=0, sticky="w", pady=(12, 0), **pad
        )
        self.on_error_menu = option_menu(
            card,
            [SKIP_FAILED, STOP_ON_ERROR],
            on_error if on_error in (SKIP_FAILED, STOP_ON_ERROR) else SKIP_FAILED,
            lambda value: self.save_settings(),
        )
        self.on_error_menu.grid(row=2, column=1, sticky="w", padx=(0, 16), pady=(12, 0))
        note(card, "When one link fails: skip it and download the rest, or stop.", 3)

        self.field_label(card, "Error log").grid(
            row=4, column=0, sticky="w", pady=(12, 16), **pad
        )
        self.ghost_button(card, "Open error log", self.open_log, width=120).grid(
            row=4, column=1, sticky="w", padx=(0, 16), pady=(12, 16)
        )

        return page

    def show_settings(self):

        self.home_page.pack_forget()
        self.settings_page.pack(fill="x")
        self.settings_button.configure(fg_color=FIELD_HOVER)

    def show_home(self):

        self.settings_page.pack_forget()
        self.home_page.pack(fill="x")
        self.settings_button.configure(fg_color=FIELD)
        self.save_settings()

    def toggle_settings(self):

        if self.settings_page.winfo_manager():
            self.show_home()
        else:
            self.show_settings()

    def retries(self):

        return RETRY_CHOICES.get(self.retry_menu.get(), 3)

    def open_log(self):

        if os.path.isfile(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            self.show_status(
                "The error log is empty: nothing has gone wrong yet.", GOOD
            )

    def create_action_area(self, main):

        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 0))

        # Download  ->  while downloading: Pause / Resume
        self.main_button = ctk.CTkButton(
            actions,
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
        self.main_button.pack(side="left", fill="x", expand=True)

        # Only shown while downloading; smaller, so it's harder to hit by mistake
        self.cancel_button = ctk.CTkButton(
            actions,
            text="Cancel",
            command=self.cancel_download,
            width=110,
            height=46,
            corner_radius=12,
            font=self.fonts["body"],
            fg_color=FIELD,
            hover_color=FIELD_HOVER,
            text_color=BAD,
            text_color_disabled=FAINT,
        )

        # Where files go: matters for every download, so always visible
        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.pack(fill="x", pady=(6, 0))

        self.footer_folder = ctk.CTkLabel(
            footer,
            text=f"Saving to  {self.short_path(self.download_folder)}",
            font=self.fonts["small"],
            text_color=MUTED,
        )
        self.footer_folder.pack(side="left")

        ctk.CTkButton(
            footer,
            text="Change",
            command=self.choose_folder,
            width=0,
            height=24,
            fg_color="transparent",
            hover_color=FIELD,
            text_color=ACCENT,
            font=self.fonts["label"],
        ).pack(side="left", padx=4)

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

        # Shown after a run where some links failed
        self.retry_button = self.ghost_button(
            row, "Retry failed", self.retry_failed, width=100
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

        # Capped: otherwise a long title widens the card, which widens the
        # wrap, which widens the window, and so on
        width = min(max(event.width - 132 - 28 - 60, 200), 440)

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
            self.footer_folder.configure(text=f"Saving to  {self.short_path(folder)}")
            self.save_settings()

    def open_folder(self):

        if os.path.isdir(self.download_folder):
            os.startfile(self.download_folder)

    def update_main_button(self):

        if self.is_downloading:
            if self.paused:
                self.main_button.configure(
                    text="▶  Resume",
                    state="normal",
                    fg_color=ACCENT,
                    hover_color=ACCENT_HOVER,
                    text_color="white",
                )
            else:
                self.main_button.configure(
                    text="❚❚  Pause",
                    state="normal",
                    fg_color=FIELD,
                    hover_color=FIELD_HOVER,
                    text_color=TEXT,
                )

            self.cancel_button.configure(
                state="disabled" if self.cancel_requested else "normal"
            )
            self.cancel_button.pack(side="left", padx=(10, 0))
            return

        self.cancel_button.pack_forget()

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
            **retry_options(self.retries()),
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

            result.update(summarize_video(info, not self.has_ffmpeg))

        except Exception as error:
            log.warning("yt-dlp lookup failed for %s: %s", url, error)
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
                log.warning("gallery-dl lookup failed for %s: %s", url, error)
                # Only matters if yt-dlp found nothing either
                if not (result["has_video"] or result["has_audio"]):
                    result["error"] = result["error"] or friendly_error(error)

        # 3. Thumbnail picture
        if result["thumbnail_url"] and not result["error"]:
            try:
                result["thumbnail"] = load_thumbnail(result["thumbnail_url"])
            except Exception as error:
                log.info("No thumbnail for %s: %s", url, error)  # that's fine

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

        # If the site is very slow, say so (and offer ↻) instead of spinning
        request = self.lookup_request
        self.root.after(45_000, self.lookup_taking_long, request)

    def lookup_taking_long(self, request):

        if request == self.lookup_request and self.lookup is None:
            self.preview_message.configure(
                text="This is taking longer than usual. The site may be slow: "
                "wait a bit more, or press ↻ to try again.",
                text_color=WARN,
            )
            self.refresh_button.grid()

    def set_thumbnail(self, image, placeholder="▶"):

        if image is None:
            self.thumbnail.configure(
                image=None, text=placeholder, width=132, height=84, fg_color=FIELD
            )

            # BUG FIX: CustomTkinter's image=None doesn't clear the label inside,
            # so it kept pointing at the old (deleted) picture and the next
            # preview crashed: stuck on "Looking up…" after a second link.
            inner_label = getattr(self.thumbnail, "_label", None)
            if inner_label is not None:
                inner_label.configure(image="")

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

        try:
            self.fill_preview(result)
        except Exception:
            # Never leave the preview half-drawn or stuck
            log.exception("Showing the preview failed for %s", result.get("url"))
            self.set_thumbnail(None, "!")
            self.preview_title.configure(text="Couldn't show this link")
            self.preview_message.configure(
                text="Something went wrong. Press ↻ to try again. "
                "(Details were saved to the error log.)",
                text_color=BAD,
            )

    def fill_preview(self, result):

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
        # BUG FIX: read the height the user picked, not the menu's current
        # text (that's "Checking…" while a new link loads)
        previous_height = self.preferred_height

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

        self.ui(show)

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
            set_process_paused(self.photo_process, True)

            self.progress.configure(progress_color=FAINT)
            self.speed_label.configure(text="")
            self.show_status(
                f"{self.item_prefix()}Paused at {self.last_fraction:.0%}", MUTED
            )
        else:
            set_process_paused(self.photo_process, False)
            self.resume_event.set()

            self.progress.configure(progress_color=ACCENT)
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

        links, problems = check_links(urls)

        if self.mode.get() == AUDIO and not self.has_ffmpeg:
            problems.append(
                "MP3 needs ffmpeg, which wasn't found. Install it "
                "(winget install Gyan.FFmpeg) and restart the app."
            )

        folder_problem = check_folder(self.download_folder, self.needed_space(links))
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
            self.url_box.focus_set()
            return

        mode = self.mode.get()

        # Catch problems now, not halfway through (wrong lines, folder, space)
        urls, problems = self.preflight(urls)

        if problems:
            self.retry_button.pack_forget()
            self.open_folder_button.pack_forget()
            self.progress.set(0)
            self.show_status("\n".join(f"•  {p}" for p in problems), BAD)
            return

        self.failed_links = []
        self.retry_button.pack_forget()
        self.is_downloading = True
        self.cancel_requested = False
        self.paused = False
        self.resume_event.set()
        self.last_fraction = 0

        self.update_main_button()
        self.status_area.pack(fill="x", pady=(14, 0))
        self.open_folder_button.pack_forget()
        self.progress.configure(progress_color=ACCENT)
        self.progress.set(0)
        self.show_status("Starting…", MUTED)

        # Read widget values here: Tk must only be touched from the main thread
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

        if self.photo_process and self.photo_process.poll() is None:
            set_process_paused(self.photo_process, False)
            self.photo_process.terminate()

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
                    failed.append((url, friendly_error(error)))

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

        with yt_dlp.YoutubeDL(options) as downloader:
            # Clean social-media titles before the file is named
            downloader.add_post_processor(CleanTitle(), when="pre_process")
            downloader.download([url])

        return 1

    def download_photo_link(self, url, cookie_args):

        return self.download_photos(url, cookie_args)

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

    def ui(self, function, *args, **kwargs):
        """Run a function on the main (Tk) thread. Safe to call from any thread.

        Background threads must not touch Tk directly, so they leave the work
        in a queue that the main thread empties every 50 ms.
        """

        self.ui_queue.put((function, args, kwargs))

    def process_ui_queue(self):

        try:
            while True:
                function, args, kwargs = self.ui_queue.get_nowait()
                try:
                    function(*args, **kwargs)
                except Exception:
                    log.exception("UI update failed")
        except queue.Empty:
            pass

        self.root.after(50, self.process_ui_queue)

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

    def on_unexpected_error(self, exc_type, exc_value, exc_traceback):
        """Tk calls this when a button/callback crashes."""

        log.error("Unexpected error", exc_info=(exc_type, exc_value, exc_traceback))
        self.show_status(
            "Something went wrong. Try again; if it keeps happening, open "
            "⚙ Settings → Error log.",
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
        self.photo_process = None
        self.progress.configure(progress_color=ACCENT)
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

        if self.failed_links and not cancelled:
            count = len(self.failed_links)
            self.retry_button.configure(
                text="Retry failed" if count == 1 else f"Retry {count} failed"
            )
            self.retry_button.pack(side="right", padx=(8, 0))

        if saved:
            self.open_folder_button.pack(side="right")

    # -------------------------------
    # UPDATES
    # -------------------------------

    def check_for_updates(self, manual=False):

        if manual:
            self.show_update_banner("Checking for updates…", can_update=False)

        def work():
            try:
                release, error = fetch_latest_release(), None
            except Exception as problem:
                release, error = None, problem

            self.ui(self.show_update_result, release, error, manual)

        threading.Thread(target=work, daemon=True).start()

    def show_update_result(self, release, error, manual):

        newer = release and parse_version(release["version"]) > parse_version(
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

        self.update_label.configure(text=text)
        self.later_button.configure(text="Later" if can_update else "Close")
        self.later_button.configure(state="normal")

        if can_update:
            self.update_button.configure(state="normal")
            self.update_button.pack(side="right", padx=(0, 8), pady=10)
        else:
            self.update_button.pack_forget()

        self.update_banner.pack(after=self.header, fill="x", pady=(14, 0))

    def hide_update_banner(self):

        self.update_banner.pack_forget()

    def start_update(self):

        release = self.update_release

        if not release:
            return

        if self.is_downloading:
            self.update_label.configure(
                text="Finish or cancel the current download first, then update."
            )
            return

        if not FROZEN:
            self.update_label.configure(
                text="Running from source: updates install only in the installed "
                "app. Use git pull instead."
            )
            return

        self.update_button.configure(state="disabled")
        self.later_button.configure(state="disabled")
        self.update_label.configure(text="Downloading update…")

        def on_progress(done, total):
            if total:
                self.ui(
                    self.update_label.configure,
                    text=f"Downloading update…  {done / total:.0%}",
                )

        def work():
            try:
                installer = download_update(release, on_progress)
            except Exception as error:
                self.ui(self.update_failed, str(error))
                return

            self.ui(self.install_update, installer)

        threading.Thread(target=work, daemon=True).start()

    def update_failed(self, message):

        self.update_label.configure(text=f"Update failed: {message}")
        self.update_button.configure(state="normal")
        self.later_button.configure(state="normal")

    def install_update(self, installer):

        self.update_label.configure(text="Installing… the app will reopen.")
        self.root.update()

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
        self.root.destroy()

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

    setup_logging()
    log.info("Video Downloader %s started", APP_VERSION)

    # Crashes in background threads go to the log too
    threading.excepthook = lambda args: log.error(
        "Background thread crashed", exc_info=args.exc_value or True
    )

    settings = VideoDownloader.load_settings()
    ctk.set_appearance_mode(settings.get("theme", "system"))

    root = ctk.CTk()
    root.title(f"Video Downloader {APP_VERSION}")

    # CustomTkinter sets its own icon shortly after start, so set ours after it
    root.after(300, set_window_icon, root)

    app = VideoDownloader(root)
    root.mainloop()
