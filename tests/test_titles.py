"""Title cleaning: one case per site/situation (equivalence classes) plus
the edges (empty, only hashtags, very long, "C#")."""

import pytest

from core import clean_title


def info(site, title, **extra):
    return {"extractor_key": site, "title": title, **extra}


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # Facebook: counters, page name and "| Facebook" removed
        (
            info(
                "Facebook",
                "1.2M views · 30K reactions | When the cat hears the fridge 😂 | Daily",
                uploader="Daily",
            ),
            "When the cat hears the fridge 😂",
        ),
        (info("Facebook", "Page | Facebook", uploader="Page"), "Facebook post by Page"),
        # Instagram: caption comes from the description
        (
            info(
                "Instagram",
                "Video by chef.amy",
                uploader="chef.amy",
                description="Easy pasta 🍝\nRecipe in bio\n#food",
            ),
            "Easy pasta 🍝",
        ),
        (
            info(
                "Instagram",
                '1,234 likes, 56 comments - x on May 1, 2026: "Galle Fort #travel"',
            ),
            "Galle Fort",
        ),
        # TikTok: hashtags removed
        (info("TikTok", "new trend?? #fyp #viral", uploader="d"), "new trend??"),
        (info("TikTok", "#fyp #foryou", uploader="d"), "TikTok post by d"),
        # YouTube: only hashtags / pure counters removed, "|" parts kept
        (info("Youtube", "Wait for the end 😱 #shorts #viral"), "Wait for the end 😱"),
        (info("Youtube", "Song | 1M views | Artist"), "Song | Artist"),
        (info("Youtube", "Shape of You | Ed Sheeran"), "Shape of You | Ed Sheeran"),
        (info("Youtube", "Top 10 views of Paris"), "Top 10 views of Paris"),
        (info("Youtube", "C# tutorial #coding"), "C# tutorial"),
        # Other sites: untouched
        (info("Vimeo", "Keep #this"), "Keep #this"),
    ],
)
def test_clean_title(data, expected):
    assert clean_title(data) == expected


def test_youtube_short_with_only_hashtags_gets_readable_name():
    data = info(
        "Youtube",
        "#shorts #trending",
        uploader="Funny Clips",
        webpage_url="https://www.youtube.com/shorts/abc",
    )
    assert clean_title(data) == "YouTube Short by Funny Clips"


def test_empty_title_never_returns_empty_text():
    assert clean_title(info("TikTok", "", uploader="")) == "TikTok post"


def test_long_caption_is_cut_at_a_word_boundary():
    title = clean_title(info("TikTok", "word " * 60))
    assert len(title) <= 101  # 100 + "…"
    assert title.endswith("…")
    assert not title.endswith(" …")
