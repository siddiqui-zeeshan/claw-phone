"""Shared pytest fixtures and configuration."""

from __future__ import annotations

import os
import sys

import pytest


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Force-kill any lingering APScheduler threads after all tests complete.

    Implemented as a tryfirst hookwrapper so the exit happens strictly AFTER
    the terminal reporter (also a wrapper) has printed its summary, and the
    flushes ensure output survives os._exit (which skips interpreter cleanup —
    without them the report is lost whenever stdout is a pipe).
    """
    yield
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exitstatus)
