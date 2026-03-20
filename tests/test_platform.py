"""Tests for spare_paw.platform module."""

from __future__ import annotations

from spare_paw.platform import (
    default_allowed_paths,
    default_shell_description,
    default_shell_executable,
    detect_platform,
    platform_label,
)


class TestDetectPlatform:
    def test_returns_valid_value(self):
        result = detect_platform()
        assert result in {"termux", "mac", "linux", "windows"}

    def test_platform_label_returns_string(self):
        label = platform_label()
        assert isinstance(label, str)
        assert len(label) > 0

    def test_default_allowed_paths_returns_list(self):
        paths = default_allowed_paths()
        assert isinstance(paths, list)
        assert len(paths) > 0
        for p in paths:
            assert isinstance(p, str)

    def test_default_shell_description_returns_string(self):
        desc = default_shell_description()
        assert isinstance(desc, str)
        assert "shell command" in desc.lower() or "execute" in desc.lower()

    def test_default_shell_executable_returns_list(self):
        exe = default_shell_executable()
        assert isinstance(exe, list)
        assert len(exe) > 0
