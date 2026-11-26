"""Python side of the phone app: a small local API around yt-dlp and core.py.

The Flutter app starts this (serious_python runs main.py) and talks to it over
HTTP on 127.0.0.1. Only the app can use it: every request must carry the
per-launch token the app passed in. Downloading itself is done by the phone
(see mobile/lib/services/downloads.dart), not here.

POST /lookup         {url}                  -> preview: title, qualities, ...
POST /resolve        {url, mode, height}    -> files to fetch + file name
POST /update-policy  {release, current}     -> core.update_policy
POST /version        {}                     -> versions
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import phone_js  # registers the phone's JavaScript engine with yt-dlp
import yt_dlp

import core


def base_options(body=None):
    """yt-dlp settings for one request. The app sends with each request:
    "js": {port, token} of its JavaScript engine, and "cookies": the file
    with the YouTube login (if signed in)."""

    body = body or {}
    js = body.get("js") or {}
    phone_js.endpoint = (
        (int(js["port"]), str(js["token"]))
        if js.get("port") and js.get("token")
        else None
    )

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "logger": core.QuietLogger(),
        "socket_timeout": 20,
        "retries": 3,
        "extractor_retries": 2,
    }
    cookies = body.get("cookies")
    if cookies and os.path.isfile(cookies):
        options["cookiefile"] = cookies
    return options


def friendly_error(error):
    """core.friendly_error, with the phone's own advice where it differs."""

    lower = str(error).lower()
    if "not a bot" in lower or "sign in to confirm" in lower:
        return (
            "YouTube wants to make sure you're not a robot. Sign in to "
            "YouTube in Settings, then try again."
        )
    return core.friendly_error(error)


def extract(url, options):
    """yt-dlp lookup (faked in tests)."""

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def first_video(info):
    """Posts with several items come back as a list: use the first one."""

    entries = [e for e in info.get("entries") or [] if e]
    return entries[0] if entries and not info.get("formats") else info


def lookup(body):
    url = (body.get("url") or "").strip()
    links, problems = core.check_links([url])

    if problems or not links:
        return {"error": problems[0] if problems else "Paste a link first."}

    try:
        info = extract(links[0], base_options(body))
    except Exception as error:
        core.log.warning("Lookup failed for %s: %s", url, error)
        return {"error": friendly_error(error)}

    summary = core.mobile_summary(info)
    return {**summary, "url": links[0], "error": None}


def resolve(body):
    url = body["url"]
    mode = body.get("mode") or core.VIDEO
    height = body.get("height")

    options = {**base_options(body), "format": core.mobile_format(mode, height)}

    try:
        info = first_video(extract(url, options))
    except Exception as error:
        core.log.warning("Resolve failed for %s: %s", url, error)
        return {"error": friendly_error(error)}

    return {
        "error": None,
        "title": core.clean_title(info) or info.get("title") or "",
        "file_name": core.mobile_file_name(info, mode),
        "media": core.mobile_media(info),
        "duration": info.get("duration") or 0,
        "thumbnail_url": info.get("thumbnail"),
    }


def update_policy(body):
    return {"policy": core.update_policy(body.get("release"), body["current"])}


def version(body):
    return {
        "yt_dlp": yt_dlp.version.__version__,
        "python": sys.version.split()[0],
        "js_runs": phone_js.runs,  # puzzles the phone's JS engine solved
    }


ROUTES = {
    "/lookup": lookup,
    "/resolve": resolve,
    "/update-policy": update_policy,
    "/version": version,
}


def make_server(token, port=0):
    """An HTTP server on 127.0.0.1 (port 0: any free port)."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("X-Token") != token:
                self.send_error(403)
                return

            route = ROUTES.get(self.path)
            if route is None:
                self.send_error(404)
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                result = route(body)
                status = 200
            except Exception as error:
                core.log.exception("Bridge request %s failed", self.path)
                result, status = {"error": friendly_error(error)}, 500

            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass  # no console noise

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve():
    """Start the API and tell the app which port it got (via a file)."""

    server = make_server(os.environ["VD_TOKEN"])
    port_file = os.environ["VD_PORT_FILE"]

    with open(port_file + ".tmp", "w") as file:
        file.write(str(server.server_address[1]))
    os.replace(port_file + ".tmp", port_file)  # the app never sees half a file

    server.serve_forever()
