"""Diagnostics: a frozen window must leave a clue in the log."""

import logging
import time

from conftest import pump
from PySide6.QtCore import QTimer

import core


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def freeze_the_window_for(seconds):
    """Stand-in for a bug that blocks the window."""

    time.sleep(seconds)


def schedule(milliseconds, function):
    QTimer.singleShot(milliseconds, function)


def test_hang_detector_logs_where_the_window_was_stuck(qapp):
    handler = ListHandler()
    core.log.addHandler(handler)
    core.log.setLevel(logging.INFO)

    stop = core.start_hang_detector(schedule, limit=1.0)
    try:
        pump(0.8)
        QTimer.singleShot(0, lambda: freeze_the_window_for(3))
        pump(4.5)  # includes the 3 s freeze, then time to recover
    finally:
        stop()
        core.log.removeHandler(handler)
        core.log.setLevel(logging.NOTSET)

    stuck = [m for m in handler.messages if "not responding" in m]
    assert stuck, handler.messages
    assert "freeze_the_window_for" in stuck[0]  # names the guilty function
    assert "Window responding again" in handler.messages


def test_no_false_alarm_when_the_window_is_fine(qapp):
    handler = ListHandler()
    core.log.addHandler(handler)

    stop = core.start_hang_detector(schedule, limit=1.0)
    try:
        pump(2.5)
    finally:
        stop()
        core.log.removeHandler(handler)

    assert not [m for m in handler.messages if "not responding" in m]
