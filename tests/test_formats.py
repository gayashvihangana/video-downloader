"""Qualities and sizes shown before downloading."""

import pytest

from main import analyse_formats, human_size, human_time, quality_text, summarize_video

AUDIO_M4A = {
    "vcodec": "none",
    "acodec": "mp4a",
    "ext": "m4a",
    "abr": 128,
    "filesize": 3_000_000,
}


def video(height, width, ext="mp4", **extra):
    return {
        "height": height,
        "width": width,
        "vcodec": "avc1",
        "acodec": "none",
        "ext": ext,
        **extra,
    }


def labels(qualities):
    return [q["label"] for q in qualities]


def test_size_is_video_plus_audio():
    formats = [video(1080, 1920, filesize=80_000_000), AUDIO_M4A]
    [quality] = analyse_formats({"formats": formats, "duration": 60}, False)
    assert quality["size"] == 83_000_000
    assert quality["approx"] is False
    assert quality_text(quality) == "1080p  ·  83 MB"


def test_size_estimated_from_bitrate_is_marked_approximate():
    formats = [video(720, 1280, tbr=1000), AUDIO_M4A]
    [quality] = analyse_formats({"formats": formats, "duration": 80}, False)
    assert quality["size"] == 10_000_000 + 3_000_000
    assert quality_text(quality).endswith("~13 MB")


def test_qualities_sorted_best_first_with_4k_label():
    formats = [video(720, 1280), video(2160, 3840, "webm"), video(1440, 2560, "webm")]
    assert labels(analyse_formats({"formats": formats}, False)) == [
        "2160p (4K)",
        "1440p",
        "720p",
    ]


def test_vertical_video_is_named_by_its_short_side():
    formats = [video(1920, 1080)]
    [quality] = analyse_formats({"formats": formats}, False)
    assert quality["label"] == "1080p"
    assert quality["height"] == 1920  # the real height is used for downloading


def test_watermarked_storyboard_and_audio_only_are_never_offered():
    formats = [
        video(1080, 1920, format_note="watermarked"),
        {"height": 90, "width": 160, "vcodec": "none", "ext": "mhtml"},
        AUDIO_M4A,
        video(720, 1280, format_note="Playback video"),
    ]
    assert labels(analyse_formats({"formats": formats}, False)) == ["720p"]


def test_without_ffmpeg_only_files_with_sound_are_offered():
    formats = [video(1080, 1920), {**video(360, 640), "acodec": "mp4a"}]
    assert labels(analyse_formats({"formats": formats}, True)) == ["360p"]


def test_no_formats_means_no_qualities():
    assert analyse_formats({}, False) == []


def test_summarize_uses_first_video_of_a_multi_item_post():
    post = {
        "extractor_key": "Instagram",
        "title": "Video by x",
        "description": "Beach day",
        "entries": [
            {
                "formats": [video(1080, 1080)],
                "duration": 12,
                "uploader": "x",
                "thumbnail": "https://img",
            },
            {"formats": [video(720, 720)]},
        ],
    }
    summary = summarize_video(post, False)
    assert summary["videos"] == 2
    assert summary["duration"] == 12
    assert summary["title"] == "Beach day"
    assert summary["thumbnail_url"] == "https://img"
    assert labels(summary["qualities"]) == ["1080p"]


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, ""),
        (999, "999 B"),
        (1000, "1 KB"),
        (9_950_000, "9.9 MB"),
        (85_123_000, "85 MB"),
        (1_530_000_000, "1.5 GB"),
    ],
)
def test_human_size_boundaries(size, text):
    assert human_size(size) == text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0:00"),
        (5, "0:05"),
        (59, "0:59"),
        (60, "1:00"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (None, "0:00"),
    ],
)
def test_human_time_boundaries(seconds, text):
    assert human_time(seconds) == text
