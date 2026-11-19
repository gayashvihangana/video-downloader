"""Error messages: every known failure maps to short, plain advice, and
unknown errors are still readable (never empty, no colour codes)."""

import pytest

from main import friendly_error


@pytest.mark.parametrize(
    ("raw", "expected_words"),
    [
        ("ERROR: [youtube] abc: Private video. Sign in", "private"),
        ("[instagram][error] HttpError: '401 Unauthorized'", "signed-in users"),
        ("ERROR: [tiktok] 123: Login required", "TikTok only shows"),
        ("ERROR: Requested format is not available", "didn't offer"),
        ("ERROR: Unsupported URL: https://example.com", "isn't supported"),
        ("Failed to decrypt with DPAPI", "Firefox"),
        ("Could not copy Chrome cookie database", "Close it completely"),
        ("Cookies file must be Netscape formatted, not JSON", "JSON"),
        ("ERROR: Sign in to confirm your age", "age-restricted"),
        ("ERROR: Sign in to confirm you're not a bot", "not a bot"),
        ("ERROR: Video unavailable", "unavailable"),
        ("HTTP Error 429: Too Many Requests", "limiting"),
        ("<urlopen error [Errno 11001] getaddrinfo failed>", "internet"),
        ("Read timed out.", "internet"),
        ("[Errno 28] No space left on device", "disk is full"),
        ("[Errno 13] Permission denied: 'C:\\x.mp4'", "blocked saving"),
    ],
)
def test_known_errors_get_plain_advice(raw, expected_words):
    assert expected_words.lower() in friendly_error(raw).lower()


def test_terminal_colour_codes_are_removed():
    message = friendly_error("\x1b[0;31mERROR:\x1b[0m [youtube] x: Something odd")
    assert "\x1b" not in message
    assert message == "Something odd"


def test_unknown_error_keeps_the_message_but_drops_help_links():
    raw = "ERROR: [tiktok] 77: Weird thing happened. Use --verbose. See  https://x"
    assert friendly_error(raw) == "Weird thing happened."


@pytest.mark.parametrize("raw", ["", "ERROR:", "[x][error]"])
def test_never_returns_empty_text(raw):
    assert friendly_error(raw).strip()
