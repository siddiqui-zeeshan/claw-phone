"""Platform detection utilities.

Shared module for detecting the current platform and providing
platform-appropriate defaults for config, tool descriptions, and paths.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def detect_platform() -> str:
    """Detect the current platform: 'termux', 'mac', 'linux', or 'windows'."""
    if os.path.exists("/data/data/com.termux"):
        return "termux"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def platform_label() -> str:
    """Human-readable platform label for prompts and descriptions."""
    labels = {
        "termux": "Android (Termux)",
        "mac": "macOS",
        "linux": "Linux",
        "windows": "Windows",
    }
    return labels.get(detect_platform(), "Linux")


def default_allowed_paths() -> list[str]:
    """Platform-appropriate default paths for the files tool."""
    plat = detect_platform()
    if plat == "termux":
        return ["/sdcard", "/data/data/com.termux/files/home"]
    if plat == "windows":
        return [str(Path.home())]
    return [str(Path.home())]


def default_shell_description() -> str:
    """Platform-appropriate description for the shell tool."""
    plat = detect_platform()
    if plat == "termux":
        return (
            "Execute a shell command on the Android phone (Termux). "
            "Use termux-api commands for device interactions: termux-battery-status, "
            "termux-location, termux-camera-photo, termux-notification, termux-tts-speak, "
            "termux-sensor, etc. Use 'su -c' for root commands."
        )
    if plat == "mac":
        return (
            "Execute a shell command on macOS. "
            "Use brew, osascript, pmset, open, defaults, and other macOS utilities."
        )
    if plat == "windows":
        return (
            "Execute a shell command on Windows. "
            "Use PowerShell cmdlets, cmd built-ins, and Windows utilities."
        )
    return "Execute a shell command on Linux."


def default_shell_executable() -> list[str]:
    """Return the shell executable to use for subprocess calls."""
    if detect_platform() == "windows" and not shutil.which("bash"):
        if shutil.which("sh"):
            return ["sh"]
        return ["cmd.exe", "/c"]
    return ["bash"]
