"""The phone app's Python side: format choice for phones, and the local API
the Flutter app talks to. No internet: yt-dlp gets fake format lists."""

import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest
import yt_dlp

import core

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "mobile", "python")
)
import bridge  # noqa: E402

# ---------- format choice ----------


def fmt(format_id, height=None, vcodec="none", acodec="none", ext="mp4", **extra):
    return {
        "format_id": format_id,
        "url": f"https://cdn.example/{format_id}",
        "height": height,
        "width": int(height * 16 / 9) if height else None,
        "vcodec": vcodec,
        "acodec": acodec,
        "ext": ext,
        "protocol": "https",
        "http_headers": {"User-Agent": "UA"},
        **extra,
    }


YOUTUBE_LIKE = [
    fmt("140", acodec="mp4a.40.2", ext="m4a", filesize=3_000_000, abr=128),
    fmt("251", acodec="opus", ext="webm", filesize=2_800_000, abr=130),
    fmt("136", 720, "avc1.4d401f", filesize=20_000_000),
    fmt("137", 1080, "avc1.640028", filesize=40_000_000),
    fmt("248", 1080, "vp9", ext="webm", filesize=30_000_000),
    fmt("271", 1440, "vp9", ext="webm", filesize=70_000_000),
    fmt("95", 720, "avc1.4d401f", "mp4a.40.2", protocol="m3u8_native"),
    fmt("18", 360, "avc1.42001E", "mp4a.40.2", filesize=8_000_000),
]


def resolve(formats, mode, height=None):
    info = {
        "id": "abc",
        "title": "Test video",
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "webpage_url": "https://youtu.be/abc",
        "formats": formats,
    }
    options = {"quiet": True, "format": core.mobile_format(mode, height)}
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.process_ie_result(info, download=False)


def ids(info):
    return [part["format_id"] for part in info.get("requested_formats") or [info]]


def test_video_picks_h264_at_the_chosen_height_plus_m4a():
    assert ids(resolve(YOUTUBE_LIKE, core.VIDEO, 720)) == ["136", "140"]
    assert ids(resolve(YOUTUBE_LIKE, core.VIDEO, 1080)) == ["137", "140"]


def test_video_never_picks_webm_or_streaming_formats():
    # 1440p exists only as VP9 WebM: the best H.264 is used instead
    assert ids(resolve(YOUTUBE_LIKE, core.VIDEO, 1440)) == ["137", "140"]
    assert ids(resolve(YOUTUBE_LIKE, core.VIDEO)) == ["137", "140"]


def test_audio_is_m4a():
    assert ids(resolve(YOUTUBE_LIKE, core.AUDIO)) == ["140"]


def test_single_file_with_sound_when_there_are_no_separate_streams():
    tiktok = [
        fmt("h264_720", 720, "h264", "aac", format_note="Playback video"),
        fmt("wm", 720, "h264", "aac", format_note="watermarked"),
    ]
    assert ids(resolve(tiktok, core.VIDEO)) == ["h264_720"]


def test_media_lists_the_files_to_fetch():
    media = core.mobile_media(resolve(YOUTUBE_LIKE, core.VIDEO, 720))
    assert [(m["kind"], m["ext"], m["size"]) for m in media] == [
        ("video", "mp4", 20_000_000),
        ("audio", "m4a", 3_000_000),
    ]
    assert media[0]["url"] == "https://cdn.example/136"
    assert media[0]["headers"]["User-Agent"] == "UA"
    assert [m["chunk_size"] for m in media] == [0, 0]


def test_media_keeps_youtubes_chunk_size():
    info = {
        "url": "https://cdn.example/v",
        "ext": "mp4",
        "vcodec": "avc1",
        "downloader_options": {"http_chunk_size": 10 << 20},
    }
    assert core.mobile_media(info)[0]["chunk_size"] == 10 << 20


def test_summary_only_offers_qualities_a_phone_can_save():
    summary = core.mobile_summary(
        {"title": "x", "extractor_key": "Youtube", "formats": YOUTUBE_LIKE}
    )
    labels = [q["label"] for q in summary["qualities"]]
    assert labels == ["1080p", "720p", "360p"]  # no 1440p (VP9 only)
    assert summary["qualities"][0]["size"] == 43_000_000  # H.264 + M4A
    assert summary["site"] == "YouTube"


ARCHIVE_LIKE = [  # direct files, no codec information (archive.org)
    {**fmt("0", 300, ext="ogv"), "vcodec": None, "acodec": None},
    {**fmt("1", 360, ext="mp4"), "vcodec": None, "acodec": None},
    {**fmt("2", 720, ext="avi"), "vcodec": None, "acodec": None},
]


def test_mp4_without_codec_information_counts_as_h264():
    assert ids(resolve(ARCHIVE_LIKE, core.VIDEO)) == ["1"]
    assert ids(resolve(ARCHIVE_LIKE, core.VIDEO, 360)) == ["1"]

    summary = core.mobile_summary({"title": "x", "formats": ARCHIVE_LIKE})
    assert [q["label"] for q in summary["qualities"]] == ["360p"]
    assert summary["has_audio_only"] is False  # no separate sound to offer


def test_audio_is_offered_only_when_the_site_has_sound_on_its_own():
    summary = core.mobile_summary({"title": "x", "formats": YOUTUBE_LIKE})
    assert summary["has_audio_only"] is True


def test_file_names():
    info = {"id": "abc", "title": 'A/B: "quoted" #tag'}
    assert core.mobile_file_name(info, core.VIDEO).endswith(" [abc].mp4")
    assert "/" not in core.mobile_file_name(info, core.VIDEO)
    assert core.mobile_file_name(info, core.AUDIO).endswith(".m4a")


# ---------- the local API ----------


@pytest.fixture
def api(monkeypatch):
    """The bridge on a free port, with yt-dlp faked."""

    def fake_extract(url, options):
        if "private" in url:
            raise yt_dlp.utils.DownloadError("ERROR: Private video. Sign in")
        info = {
            "id": "abc",
            "title": "Test video",
            "extractor_key": "Youtube",
            "uploader": "someone",
            "duration": 60,
            "thumbnail": "https://img/abc.jpg",
            "webpage_url": url,
            "formats": YOUTUBE_LIKE,
        }
        if options.get("format"):
            with yt_dlp.YoutubeDL({"quiet": True, **options}) as ydl:
                return ydl.process_ie_result(info, download=False)
        return info

    monkeypatch.setattr(bridge, "extract", fake_extract)
    server = bridge.make_server(token="secret")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(base, path, body=None, token="secret"):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json", "X-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def test_lookup(api):
    result = call(api, "/lookup", {"url": "https://youtu.be/abc"})
    assert result["title"] == "Test video"
    assert result["uploader"] == "someone"
    assert [q["label"] for q in result["qualities"]] == ["1080p", "720p", "360p"]
    assert result["error"] is None


def test_lookup_error_is_friendly(api):
    result = call(api, "/lookup", {"url": "https://youtu.be/private"})
    assert "private" in result["error"].lower()


def test_lookup_rejects_text_that_isnt_a_link(api):
    result = call(api, "/lookup", {"url": "hello there"})
    assert result["error"]


def test_resolve_gives_files_and_a_name(api):
    result = call(
        api, "/resolve", {"url": "https://youtu.be/abc", "mode": "Video", "height": 720}
    )
    assert result["file_name"] == "Test video [abc].mp4"
    assert [m["kind"] for m in result["media"]] == ["video", "audio"]


def test_update_policy(api):
    release = {"version": "9.0.0", "published": 0}
    result = call(api, "/update-policy", {"release": release, "current": "1.0.0"})
    assert result == {"policy": {"required": True, "days_left": 0}}


def test_wrong_token_is_refused(api):
    with pytest.raises(urllib.error.HTTPError) as error:
        call(api, "/version", token="wrong")
    assert error.value.code == 403


def test_version(api):
    result = call(api, "/version")
    assert result["yt_dlp"] == yt_dlp.version.__version__


# ---------- YouTube's checks: the phone's JS engine and the login ----------


def test_js_engine_and_login_reach_yt_dlp(tmp_path):
    cookies = tmp_path / "youtube_cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")

    options = bridge.base_options(
        {"js": {"port": 5000, "token": "t"}, "cookies": str(cookies)}
    )
    assert bridge.phone_js.endpoint == (5000, "t")
    assert options["cookiefile"] == str(cookies)

    # Not signed in / no engine: nothing is passed
    options = bridge.base_options({"cookies": str(tmp_path / "missing.txt")})
    assert bridge.phone_js.endpoint is None
    assert "cookiefile" not in options


def test_phone_js_engine_is_offered_to_yt_dlp_only_when_running():
    ydl = yt_dlp.YoutubeDL({"quiet": True})
    ie = ydl.get_info_extractor("Youtube")
    ie.initialize()
    phone = ie._jsc_director.providers["Phone"]

    bridge.phone_js.endpoint = None
    assert not phone.is_available()
    bridge.phone_js.endpoint = (5000, "t")
    try:
        assert phone.is_available()
    finally:
        bridge.phone_js.endpoint = None


def test_bot_check_says_to_sign_in(api, monkeypatch):
    def blocked(url, options):
        raise yt_dlp.utils.DownloadError(
            "ERROR: [youtube] abc: Sign in to confirm you’re not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication."
        )

    monkeypatch.setattr(bridge, "extract", blocked)
    result = call(api, "/lookup", {"url": "https://youtu.be/abc"})
    assert "Sign in to YouTube in Settings" in result["error"]
