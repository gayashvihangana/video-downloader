# Testing (QA)

How Video Downloader is tested, and what to check before each release.

## Quick start

```
pip install -r requirements-dev.txt
pytest                      # ~110 tests, ~10 s, no internet needed
set RUN_LIVE=1 && pytest -m live   # real YouTube checks (before a release)
ruff check . && ruff format --check .
```

Tests also run automatically on GitHub for every push (Actions → **tests**).

## QA principles used

| Principle | How it's applied here |
|---|---|
| **Test pyramid** | Many fast unit tests, fewer UI tests, a couple of live internet tests, and a short manual checklist. |
| **Isolation** | Tests never touch the real settings file or downloads folder (`tmp_path`). The network, gallery-dl and downloads are faked, so results don't depend on the sites. |
| **Equivalence classes** | One case per kind of input, e.g. a title from each site, each kind of error message. |
| **Boundary values** | Edges where bugs hide: 999 B / 1 KB, 59 s / 60 s / 1 h, disk space exactly at the limit, version 1.10 vs 1.9. |
| **Regression tests** | Every bug found gets a test that fails if the bug comes back (see the defect log). |
| **Verify the tests** | The BUG-001 tests were run against the old code to prove they fail without the fix. |
| **No flaky tests** | Tests wait for a condition, never for a fixed time. The suite was run 5× in a row with the same result. |
| **Fail safe** | Unexpected errors are logged (`%LOCALAPPDATA%\VideoDownloader\error.log`) and shown as a plain message. The app never gets stuck. |
| **Error prevention** | Before downloading, the app checks the links, the save folder and the free disk space. |

## Test levels

| Level | Files | What it covers |
|---|---|---|
| Unit | `test_titles.py`, `test_errors.py`, `test_formats.py`, `test_checks.py` | Title cleaning, error messages, qualities and sizes, link, folder and space checks, version compare |
| Integration (faked network) | `test_photos_and_updates.py` | Photo posts (gallery-dl output), update check, update download and checksum |
| UI | `test_app.py` | The real window (drawn off-screen): preview, modes, quality, Home → Downloads queue (3 at once), pause/resume/cancel per download, skip/stop on errors, retry, recent downloads kept after restart, settings, update banner, fits a 1366×768 screen |
| Live (opt-in) | `test_live.py` | Real YouTube lookup, thumbnail and download |
| Manual | checklist below | Installer, real sites, sign-in, things that need a person |

## Defect log

| ID | Bug | Root cause | Fix | Regression test |
|---|---|---|---|---|
| BUG-001 | After a download (or any link with a thumbnail), the next link's preview stayed on "Looking up the link…" | (CustomTkinter UI) `image=None` didn't clear the inner label, which kept pointing at the deleted thumbnail, so the next update crashed halfway | Clear the old picture, and catch any error while drawing the preview. The Qt UI keeps its own copy of each picture | `test_bug001_second_link_after_a_thumbnail_shows_its_preview`, `test_error_after_a_thumbnail_does_not_get_stuck` |
| BUG-002 | "Disk full" errors said "cookies file can't be found" | `"errno 2"` also matched `"errno 28"` | Match whole words (`\berrno 2\b`) | `test_known_errors_get_plain_advice[...No space left...]` |
| BUG-003 | The chosen quality wasn't kept for the next link | The menu showed "Checking…" while loading, so the old choice was read too late | Remember the height the user picked | `test_quality_menu_lists_sizes_and_remembers_height` |
| BUG-004 | A long title made the window wider and wider | Title wrap width followed the card width, and the card grew with the title | Cap the wrap width | (visual check) |
| UX-001 | Window taller than small laptop screens; the progress area was cut off | Every setting was on the main screen | Settings moved to a ⚙ Settings page; save folder stays visible | `test_window_fits_a_small_laptop_screen` |
| BUG-005 | Long error messages made the window wider on scaled displays (125%) | (CustomTkinter UI) wrap width mixed screen pixels with scaled points | Qt labels word-wrap to the space they have | `test_bug005_long_messages_do_not_widen_the_window` |
| BUG-006 | A user's videos played **sound only** (black picture) | The app picked AV1 (YouTube) or HEVC (TikTok) for the smallest file, and many Windows PCs can't decode those | Default "Plays everywhere": prefer H.264 + AAC. Sizes with no H.264 version are marked "converts" and converted with ffmpeg after downloading. "Smaller files" setting for the old behaviour | `tests/test_playable.py` (includes a real VP9 → H.264 conversion) |
| UX-002 | Sign-in was a permanent setting most people never need | Not shown where it matters | Sign-in panel appears only on links that need an account. Installed browsers only, Firefox first, auto-retry, sign out from the chip | `tests/test_sign_in.py` |
| ARCH-001 | Background threads updated the window directly | UI toolkits aren't thread-safe | Threads hand work to the UI thread through a queued Qt signal (`VideoDownloader.ui`) | all UI tests, `test_progress_from_the_download_thread_reaches_the_window` |

## Manual checklist (before each release)

Do this on the **installed** app (`release\VideoDownloader-Setup-x.y.z.exe`), ideally on a second PC.

**Install and update**
- [ ] Setup runs without an admin password. The Start menu shortcut works.
- [ ] Windows "protected your PC": More info → Run anyway works.
- [ ] Publishing a newer release shows the update banner. **Update now** installs and reopens the app.
- [ ] Uninstall (Windows Settings → Apps) removes the app.

**Downloads** (one link per row)

| Site | Lookup + thumbnail | Video | Audio (MP3) | Photos |
|---|---|---|---|---|
| YouTube video | [ ] | [ ] | [ ] | – |
| YouTube Short | [ ] | [ ] | [ ] | – |
| TikTok video (no watermark) | [ ] | [ ] | [ ] | – |
| TikTok photo slideshow | [ ] | – | [ ] | [ ] |
| Instagram reel / post (signed in) | [ ] | [ ] | [ ] | [ ] |
| Facebook video | [ ] | [ ] | [ ] | – |

**Plays everywhere (BUG-006)**
- [ ] A downloaded YouTube 1080p video shows the **picture** in the Windows Media Player / Photos app on a PC without extra codecs.
- [ ] Picking 4K / 1440p shows "converts", the status shows "Converting… %", and the result plays with picture.
- [ ] A TikTok download shows the picture.

**Error handling**
- [ ] Wi-Fi off, then download: "Can't reach the site…", and the app still works.
- [ ] A private / deleted video gives a clear message, not a crash.
- [ ] Three links with one bad one: "Skip it" saves 2, then **Retry failed** works. "Stop everything" stops.
- [ ] A line that isn't a link is reported before anything starts.
- [ ] Save folder on a full USB stick or a read-only folder gives a clear message.
- [ ] Pause for a minute, then Resume continues. Cancel while paused works.

**Downloads page**
- [ ] Download on Home: it says "Download started", **Go to Downloads** opens the page, and Home is empty for the next link.
- [ ] Five links at once: 3 download, 2 wait; the sidebar badge counts them.
- [ ] A finished download: ▶ opens the file, the folder button shows it selected in File Explorer.
- [ ] Close and reopen the app: Recent downloads are still listed. **Clear list** keeps the files.
- [ ] Updating 1.1.1 → 2.0.0 with the installer: the app works, settings are kept, and no CustomTkinter / tcl folders are left in the install folder.
- [ ] Download, cancel, paste another link: the preview and thumbnail show (BUG-001).
- [ ] ⚙ Settings → Open error log opens the log.

**Sign-in**
- [ ] A normal public video shows no sign-in anywhere.
- [ ] A private / login-only post shows "🔒 Sign in to view this" with only the browsers installed on that PC.
- [ ] Signed in to the site in Firefox: click Firefox, then the link reloads by itself and works.
- [ ] A browser not signed in to that site explains what to do.
- [ ] The "🔒 Firefox ✕" chip signs out.

## Reporting a bug

Please include:

1. **Steps** to make it happen (with the link if you can).
2. **What you expected** and **what happened** (screenshot).
3. App version (Settings → About, e.g. 2.0.0).
4. The last lines of the error log (⚙ Settings → Open error log).
