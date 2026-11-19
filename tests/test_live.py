"""Real-internet checks (slow, depend on the sites). Skipped by default.

Run them before a release:   set RUN_LIVE=1  then  pytest -m live
"""

import pytest
import yt_dlp

import main

pytestmark = pytest.mark.live

YOUTUBE = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 s


def options(**extra):
    return {
        "quiet": True,
        "logger": main.QuietLogger(),
        "js_runtimes": {"deno": {}, "node": {}},
        **extra,
    }


def test_youtube_lookup_finds_qualities_and_thumbnail():
    with yt_dlp.YoutubeDL(options()) as downloader:
        info = downloader.extract_info(YOUTUBE, download=False)

    summary = main.summarize_video(info, need_audio_in_file=False)
    assert summary["title"] == "Me at the zoo"
    assert summary["qualities"]
    assert main.load_thumbnail(summary["thumbnail_url"]).width > 0


def test_youtube_download_with_clean_file_name(tmp_path):
    template = str(tmp_path / "%(title).90B [%(id)s].%(ext)s")
    with yt_dlp.YoutubeDL(options(outtmpl=template, format="b")) as downloader:
        downloader.add_post_processor(main.CleanTitle(), when="pre_process")
        downloader.download([YOUTUBE])

    assert [p.name for p in tmp_path.iterdir()] == ["Me at the zoo [jNQXAC9IVRw].mp4"]
