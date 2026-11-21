"""Video Downloader: start here.

python main.py                   open the app
VideoDownloader.exe --gallery-dl  behave like the gallery-dl tool (used by
                                  the installed app for photo downloads)
"""

import sys
import threading

import core


def run_gallery_dl_cli():
    """VideoDownloader.exe --gallery-dl <args>: behave like the gallery-dl tool."""

    import gallery_dl

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")

    sys.argv = ["gallery-dl", *sys.argv[2:]]
    sys.exit(gallery_dl.main())


def set_app_id():
    """Give the app its own taskbar button and icon (not python.exe's)."""

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "VideoDownloader.App"
        )
    except (AttributeError, OSError):
        pass


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--gallery-dl":
        run_gallery_dl_cli()

    core.setup_logging()
    core.log.info("Video Downloader %s started", core.APP_VERSION)

    # Crashes in background threads go to the log too
    threading.excepthook = lambda args: core.log.error(
        "Background thread crashed", exc_info=args.exc_value or True
    )

    set_app_id()

    # Qt is imported only here, so `--gallery-dl` starts fast
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui import theme
    from ui.window import VideoDownloader

    app = QApplication(sys.argv)
    app.setApplicationName("Video Downloader")
    app.setApplicationVersion(core.APP_VERSION)

    theme.apply_theme(VideoDownloader.load_settings().get("theme", "system"))

    window = VideoDownloader()

    # A crash in a button click or other UI callback: log it and say so,
    # instead of failing silently
    sys.excepthook = window.on_unexpected_error

    # If the window ever freezes, the log will say exactly where
    core.start_hang_detector(lambda ms, function: QTimer.singleShot(ms, function))

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
