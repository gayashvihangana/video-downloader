"""Everything the app does, without any window code.

Links, formats, downloads (yt-dlp / gallery-dl), sign-in, error messages and
updates live here, so they can be tested and reused without a UI. The window
is in the ui package.
"""

import ctypes
import faulthandler
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler

from PIL import Image
from yt_dlp.postprocessor.common import PostProcessor

# -------------------------------
# APP INFO
# -------------------------------

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".video_downloader.json")

APP_VERSION = "2.0.0"

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

# Video format setting. Many PCs can't show AV1/VP9/HEVC video (sound only,
# black picture), so by default the app gets H.264 or converts to it.
PLAYS_EVERYWHERE = "Plays everywhere (H.264)"
SMALLER_FILES = "Smaller files (AV1 / VP9)"

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
CRASH_FILE = os.path.join(LOG_DIR, "crash.log")

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

    # Hard crashes (Python itself dying) skip normal error handling; this
    # still writes where it happened to crash.log
    global crash_file
    try:
        crash_file = open(CRASH_FILE, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=crash_file, all_threads=True)
    except OSError:
        pass


crash_file = None  # kept open for faulthandler while the app runs


def start_hang_detector(schedule, limit=8.0):
    """If the window stops responding for `limit` seconds, write to the log
    exactly where it was stuck. Returns a function that stops the detector.

    schedule(milliseconds, function) must run function later on the UI
    thread (e.g. QTimer.singleShot), so it only runs while the UI is alive.
    """

    state = {"beat": time.monotonic(), "running": True}
    main_thread = threading.main_thread().ident

    def beat():
        state["beat"] = time.monotonic()
        if state["running"]:
            schedule(500, beat)

    def watch():
        reported = False

        while state["running"]:
            time.sleep(min(2.0, limit / 4))
            stuck = time.monotonic() - state["beat"]

            if stuck > limit and not reported:
                frame = sys._current_frames().get(main_thread)
                where = "".join(traceback.format_stack(frame)) if frame else "?"
                log.error(
                    "Window not responding for %.0f s. It was busy here:\n%s",
                    stuck,
                    where,
                )
                reported = True

            elif stuck <= limit and reported:
                log.info("Window responding again")
                reported = False

    def stop():
        state["running"] = False

    schedule(500, beat)
    threading.Thread(target=watch, daemon=True).start()

    return stop


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

# Where each browser keeps its profile: used to offer only installed browsers
BROWSER_PROFILES = {
    "Firefox": ("APPDATA", "Mozilla", "Firefox", "Profiles"),
    "Chrome": ("LOCALAPPDATA", "Google", "Chrome", "User Data"),
    "Edge": ("LOCALAPPDATA", "Microsoft", "Edge", "User Data"),
    "Brave": ("LOCALAPPDATA", "BraveSoftware", "Brave-Browser", "User Data"),
}

# Words in an error that mean "this needs an account"
SIGN_IN_WORDS = (
    "private video",
    "sign in to confirm",
    "not a bot",
    "login required",
    "login_required",
    "log in",
    "logged-in",
    "use --cookies",
    "cookies-from-browser",
    "401",
    "unauthorized",
    "members-only",
    "join this channel",
    "age-restricted",
    "empty media response",
)

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


def installed_browsers():
    """Browsers that have a profile on this PC (so they can have a login)."""

    found = []

    for name, (variable, *parts) in BROWSER_PROFILES.items():
        base = os.environ.get(variable)
        if base and os.path.isdir(os.path.join(base, *parts)):
            found.append(name)

    return found


def needs_sign_in(error):
    """True if this error means the post/video needs a logged-in account."""

    lower = str(error).lower()
    return any(word in lower for word in SIGN_IN_WORDS)


def site_of(url):
    """ "https://www.instagram.com/p/x" -> "Instagram" (None if unknown)."""

    lower = (url or "").lower()

    for site, (domain, _) in SITE_LOGIN_COOKIES.items():
        if domain in lower or (site == "YouTube" and "youtu.be" in lower):
            return site

    if "fb.watch" in lower:
        return "Facebook"

    return None


def read_logins(method, cookie_file=None):
    """Sites the browser / cookies file is signed in to (reads this PC only)."""

    from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser

    if method == COOKIES_FILE:
        jar = YoutubeDLCookieJar(cookie_file)
        jar.load()
    else:
        jar = extract_cookies_from_browser(method.lower(), logger=QuietLogger())

    cookies = [cookie for cookie in jar if not cookie.is_expired()]

    return [
        site
        for site, (domain, names) in SITE_LOGIN_COOKIES.items()
        if any(c.domain.endswith(domain) and c.name in names for c in cookies)
    ]


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
            "sign in to download it."
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
            f"{site} only shows this to signed-in users. Sign in with the browser "
            f"where you use {site}."
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


STANDARD_HEIGHTS = (144, 240, 360, 480, 540, 576, 720, 1080, 1440, 2160, 4320)


def quality_name(width, height):
    """The standard name for a resolution ("1080p"), the way YouTube names it.

    Wide videos are shorter than their quality: a cinema-shaped 1920x804 is
    1080p, not "804p". So each size is compared with the 16:9 picture it
    fills (by width or height, whichever is bigger), and named after the
    biggest standard size it reaches. Vertical videos (Shorts) are measured
    the same way, turned sideways.
    """

    width = width or height
    long_side, short_side = max(width, height), min(width, height)
    fills = max(short_side, long_side * 9 / 16)

    # A few pixels short of a standard size still counts (1918x1078 is 1080p)
    reached = [h for h in STANDARD_HEIGHTS if h <= fills * 1.03]
    standard = reached[-1] if reached else round(short_side)

    label = f"{standard}p"
    if standard >= 4320:
        label += " (8K)"
    elif standard >= 2160:
        label += " (4K)"
    return label


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

        label = quality_name(fmt.get("width"), height)

        size, approx = format_size(fmt, duration)
        if not has_audio and best_audio:
            size += audio_size
            approx = approx or audio_approx

        h264 = is_h264(fmt.get("vcodec"))

        # Prefer what yt-dlp will pick for this height: MP4, then higher fps/bitrate
        rank = (fmt.get("ext") == "mp4", fmt.get("fps") or 0, fmt.get("tbr") or 0)
        current = by_label.setdefault(
            label,
            {
                "label": label,
                "height": height,
                "size": size,
                "approx": approx,
                "rank": rank,
                "h264": False,  # is there a version every player can show?
                "h264_size": 0,
                "h264_approx": True,
                "h264_rank": None,
            },
        )

        if rank > current["rank"]:
            current.update(height=height, size=size, approx=approx, rank=rank)

        if h264 and (current["h264_rank"] is None or rank > current["h264_rank"]):
            current.update(
                h264=True, h264_size=size, h264_approx=approx, h264_rank=rank
            )

    return sorted(by_label.values(), key=lambda q: q["height"], reverse=True)


def is_h264(vcodec):
    """H.264 ("avc1…") plays on every PC, phone and TV; AV1/VP9/HEVC don't."""

    return (vcodec or "").lower().startswith(("avc", "h264"))


def convert_to_h264(path, duration, on_progress, start_process):
    """Re-encode a video file to H.264 + AAC in place (so every player shows
    the picture). start_process(command) must return a Popen with stdout."""

    temp = path + ".converting.mp4"
    command = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-i",
        path,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",  # 10-bit / HDR sources won't play everywhere otherwise
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        temp,
    ]

    process = start_process(command)

    for line in process.stdout:
        if line.startswith("out_time_us=") and duration:
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue  # "N/A" at the very start
            on_progress(min(max(seconds / duration, 0), 1))

    process.wait()

    if process.returncode:
        if os.path.exists(temp):
            os.remove(temp)
        raise RuntimeError("Converting the video so it plays everywhere failed.")

    os.replace(temp, path)


class MakePlayable(PostProcessor):
    """After download: convert to H.264 if the video isn't already."""

    def __init__(self, convert):
        super().__init__()
        self.convert = convert  # function(path, duration)

    def run(self, information):
        vcodec = information.get("vcodec")
        path = information.get("filepath")

        if path and vcodec not in (None, "none") and not is_h264(vcodec):
            self.convert(path, information.get("duration") or 0)
            information["vcodec"] = "avc1"

        return [], information


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


def quality_text(quality, plays_everywhere=False):
    """ "1080p  ·  83 MB". In "plays everywhere" mode it shows the H.264 size,
    or "converts" when there's no H.264 version (it'll be converted)."""

    if plays_everywhere and quality.get("h264"):
        size, approx = quality["h264_size"], quality["h264_approx"]
    else:
        size, approx = quality["size"], quality["approx"]

    text = quality["label"]

    if size:
        text += f"  ·  {'~' if approx else ''}{human_size(size)}"

    if plays_everywhere and not quality.get("h264", True):
        text += "  ·  converts"

    return text


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
