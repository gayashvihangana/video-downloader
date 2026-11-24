"""Package the Python side and run Flutter for the phone app.

    python tool/build.py android run              # on the emulator / phone
    python tool/build.py android build apk --release --split-per-abi
    python tool/build.py ios build ios --simulator --no-codesign   (macOS)
    python tool/build.py android test integration_test
    python tool/build.py android --skip-package run   (Python unchanged: faster)

What it does:
1. Stages mobile/python/*.py together with the repo's core.py (one copy of the
   app's logic for desktop and phone; nothing is duplicated in git).
2. Packages them with yt-dlp for the phone (serious_python).
3. Runs `flutter <args>` with the same settings, so the bundled Python and
   its packages match.
"""

import os
import shutil
import subprocess
import sys

MOBILE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(MOBILE)
BUILD = os.path.join(MOBILE, "build", "python")

PYTHON_VERSION = "3.14"  # same as the desktop app
BUNDLE_ID = "io.github.gayashvihangana.videoDownloader"

# Pure-Python packages only (phones have no compiler); certifi gives Python
# the certificates for HTTPS.
REQUIREMENTS = ["yt-dlp", "yt-dlp-ejs", "certifi"]

PLATFORMS = {"android": "Android", "ios": "iOS"}


def run(command, env):
    print("+", " ".join(command), flush=True)
    # Windows needs the shell to find flutter.bat / dart.bat
    result = subprocess.run(command, cwd=MOBILE, env=env, shell=os.name == "nt")
    if result.returncode:
        sys.exit(result.returncode)


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in PLATFORMS:
        sys.exit(__doc__)

    platform, flutter_args = PLATFORMS[sys.argv[1]], sys.argv[2:]
    skip_package = "--skip-package" in flutter_args
    if skip_package:
        flutter_args.remove("--skip-package")

    env = {
        **os.environ,
        "SERIOUS_PYTHON_VERSION": PYTHON_VERSION,
        "SERIOUS_PYTHON_SITE_PACKAGES": os.path.join(BUILD, "site-packages"),
        "SERIOUS_PYTHON_APP": os.path.join(BUILD, "app"),
        "SERIOUS_PYTHON_BUNDLE_ID": BUNDLE_ID,
    }

    if not skip_package:
        package_python(platform, env)

    run(["flutter", *flutter_args], env)


def package_python(platform, env):
    source = os.path.join(BUILD, "src")
    shutil.rmtree(source, ignore_errors=True)
    os.makedirs(source)
    for name in os.listdir(os.path.join(MOBILE, "python")):
        if name.endswith(".py"):
            shutil.copy2(os.path.join(MOBILE, "python", name), source)
    shutil.copy2(os.path.join(REPO, "core.py"), source)

    package = ["dart", "run", "serious_python:main", "package", source, "-p", platform]
    for requirement in REQUIREMENTS:
        package += ["-r", requirement]
    run(package, env)


if __name__ == "__main__":
    main()
