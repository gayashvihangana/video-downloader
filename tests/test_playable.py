"""BUG-006: videos played with sound only (black picture) on some PCs, because
the app downloaded AV1/VP9/HEVC. Now it prefers H.264, or converts to it."""

import json
import shutil
import subprocess

import pytest

import main
from main import MakePlayable, analyse_formats, convert_to_h264, is_h264, quality_text

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def video(height, vcodec, size, **extra):
    return {
        "height": height,
        "width": height * 16 // 9,
        "vcodec": vcodec,
        "acodec": "none",
        "ext": "mp4",
        "filesize": size,
        **extra,
    }


AUDIO = {
    "vcodec": "none",
    "acodec": "mp4a.40.2",
    "ext": "m4a",
    "abr": 128,
    "filesize": 1_000_000,
}


@pytest.mark.parametrize(
    ("vcodec", "expected"),
    [
        ("avc1.64002A", True),
        ("h264", True),
        ("av01.0.08M.08", False),
        ("vp09.00.51.08", False),
        ("hev1", False),
        ("bytevc1", False),
        (None, False),
    ],
)
def test_is_h264(vcodec, expected):
    assert is_h264(vcodec) is expected


def test_menu_shows_h264_size_and_marks_sizes_that_need_converting():
    formats = [
        video(2160, "vp09.00.51.08", 900_000_000),
        video(1080, "av01.0.08M.08", 100_000_000),
        video(1080, "avc1.64002A", 150_000_000),
        AUDIO,
    ]
    k4, hd = analyse_formats({"formats": formats}, False)

    assert quality_text(hd, plays_everywhere=True) == "1080p  ·  151 MB"
    assert (
        quality_text(k4, plays_everywhere=True) == "2160p (4K)  ·  901 MB  ·  converts"
    )
    # "Smaller files" mode: no conversion note
    assert quality_text(k4, plays_everywhere=False) == "2160p (4K)  ·  901 MB"


def test_make_playable_converts_only_non_h264():
    converted = []
    step = MakePlayable(lambda path, duration: converted.append(path))

    for vcodec, path in [
        ("avc1.4D401F", "a.mp4"),
        ("av01.0.08M.08", "b.mp4"),
        ("none", "c.mp3"),
        ("vp09.00.51.08", "d.mp4"),
    ]:
        _, info = step.run({"vcodec": vcodec, "filepath": path, "duration": 5})

    assert converted == ["b.mp4", "d.mp4"]
    assert info["vcodec"] == "avc1"  # recorded as converted


def probe_codec(path):
    output = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    stream = json.loads(output)["streams"][0]
    return stream["codec_name"], stream["pix_fmt"]


@needs_ffmpeg
def test_real_conversion_of_a_vp9_video_to_h264(tmp_path):
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x180:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=2",
            "-c:v",
            "libvpx-vp9",
            "-c:a",
            "libopus",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    assert probe_codec(source)[0] == "vp9"

    steps = []

    def start(command):
        return subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )

    convert_to_h264(str(source), 2, steps.append, start)

    assert probe_codec(source) == ("h264", "yuv420p")
    assert steps and steps[-1] == pytest.approx(1, abs=0.15)
    assert not list(tmp_path.glob("*.converting.mp4"))  # temp file cleaned up


@needs_ffmpeg
def test_failed_conversion_keeps_the_original(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    def start(command):
        return subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )

    with pytest.raises(RuntimeError, match="plays everywhere"):
        convert_to_h264(str(broken), 2, lambda f: None, start)

    assert broken.read_bytes() == b"not a video"
    assert not list(tmp_path.glob("*.converting.mp4"))


# ---------- in the app ----------


def test_default_choice_needs_no_converting(app):
    formats = [
        video(2160, "vp09.00.51.08", 900_000_000),
        video(1080, "avc1.64002A", 150_000_000),
        AUDIO,
    ]
    app.lookup = {"duration": 10}
    app.set_quality_choices(analyse_formats({"formats": formats}, False))

    assert app.quality.get().startswith("1080p")  # not the 4K that converts


def test_download_options_follow_the_video_format_setting(app):
    app.video_format_menu.set(main.PLAYS_EVERYWHERE)
    options = app.get_download_options()
    assert options["format_sort"][:2] == ["res", "vcodec:h264"]
    assert options["make_playable"] is True

    app.video_format_menu.set(main.SMALLER_FILES)
    options = app.get_download_options()
    assert "vcodec:h264" not in options["format_sort"]
    assert "make_playable" not in options
