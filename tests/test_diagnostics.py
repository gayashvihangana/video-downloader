"""Diagnostics: a frozen window must leave a clue in the log."""

import logging
import time

from conftest import pump

import main


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def freeze_the_window_for(seconds):
    """Stand-in for a bug that blocks the window."""

    time.sleep(seconds)


def test_hang_detector_logs_where_the_window_was_stuck(tk_root):
    handler = ListHandler()
    main.log.addHandler(handler)
    main.log.setLevel(logging.INFO)

    stop = main.start_hang_detector(tk_root, limit=1.0)
    try:
        pump(tk_root, 0.8)
        tk_root.after(0, freeze_the_window_for, 3)
        pump(tk_root, 4.5)  # includes the 3 s freeze, then time to recover
    finally:
        stop()
        main.log.removeHandler(handler)
        main.log.setLevel(logging.NOTSET)

    stuck = [m for m in handler.messages if "not responding" in m]
    assert stuck, handler.messages
    assert "freeze_the_window_for" in stuck[0]  # names the guilty function
    assert "Window responding again" in handler.messages


def test_no_false_alarm_when_the_window_is_fine(tk_root):
    handler = ListHandler()
    main.log.addHandler(handler)

    stop = main.start_hang_detector(tk_root, limit=1.0)
    try:
        pump(tk_root, 2.5)
    finally:
        stop()
        main.log.removeHandler(handler)

    assert not [m for m in handler.messages if "not responding" in m]
