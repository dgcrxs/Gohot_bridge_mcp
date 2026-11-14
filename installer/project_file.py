"""Line based editing of Godot's ``project.godot``.

``project.godot`` is an INI-like file that Godot rewrites itself. The installer
must not reserialise it: comments, ordering, padding and line endings all have
to survive, so this module works line by line and only ever replaces the single
``enabled=`` line of the ``[editor_plugins]`` section.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Sequence

from . import BACKUP_SUFFIX, PLUGIN_RES_PATH

#: Name of the Godot project file.
PROJECT_FILE = "project.godot"
#: Backup created before the installer touches the project (created once).
PROJECT_BACKUP = PROJECT_FILE + BACKUP_SUFFIX
#: Section holding the enabled editor plugins.
PLUGINS_SECTION = "editor_plugins"

_ENABLED_LINE_RE = re.compile(r"^\s*enabled\s*=\s*PackedStringArray\s*\((.*)\)\s*$")
_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")
_STRING_ITEM_RE = re.compile(r'"([^"]*)"')
_CONFIG_VERSION_RE = re.compile(r"^\s*config_version\s*=\s*(\d+)\s*$", re.MULTILINE)


# ------------------------------------------------------------------------- io


def read_text(path: Path) -> str:
    """Read a file preserving every byte (surrogateescape round trip)."""
    return Path(path).read_bytes().decode("utf-8", errors="surrogateescape")


def write_text(path: Path, text: str) -> None:
    """Write ``text`` back byte for byte, atomically when possible."""
    target = Path(path)
    data = text.encode("utf-8", errors="surrogateescape")
    temp = target.with_name(target.name + ".tmp-mcp")
    try:
        temp.write_bytes(data)
        os.replace(str(temp), str(target))
    except OSError:
        # Windows refuses os.replace while the editor holds the file open.
        if temp.exists():
            try:
                temp.unlink()
            except OSError:  # pragma: no cover - best effort
                pass
        target.write_bytes(data)


# -------------------------------------------------------------------- parsing


def detect_newline(text: str) -> str:
    """Return the dominant line ending of ``text``."""
    return "\r\n" if "\r\n" in text else "\n"


def parse_packed_items(raw: str) -> List[str]:
    """Extract the quoted items of a ``PackedStringArray(...)`` body."""
    return [match.group(1) for match in _STRING_ITEM_RE.finditer(raw)]


def format_packed(items: Sequence[str]) -> str:
    """Render a ``PackedStringArray(...)`` value the way Godot writes it."""
    if not items:
        return "PackedStringArray()"
    return "PackedStringArray(" + ", ".join('"%s"' % item for item in items) + ")"


def section_bounds(lines: Sequence[str], section: str) -> tuple:
    """Return ``(header_index, end_index)`` of ``[section]`` or ``(-1, -1)``."""
    header = -1
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line.strip())
        if not match:
            continue
        name = match.group("name").strip()
        if header < 0:
            if name.lower() == section.lower():
                header = index
            continue
        return header, index
    if header >= 0:
        return header, len(lines)
    return -1, -1


def editor_plugins_enabled(text: str) -> List[str]:
    """Return the items of ``[editor_plugins] enabled=...`` (may be empty)."""
    lines = text.splitlines(keepends=True)
    header, end = section_bounds(lines, PLUGINS_SECTION)
    if header < 0:
        return []
    for line in lines[header + 1 : end]:
        match = _ENABLED_LINE_RE.match(line.rstrip("\r\n"))
        if match:
            return parse_packed_items(match.group(1))
    return []


def set_enabled_items(text: str, items: Sequence[str]) -> str:
    """Return ``text`` with the plugin array replaced by ``items``.

    Only the ``enabled=`` line is rewritten, every other line (comments,
    ordering, line endings) is kept untouched. A missing section or key is
    created at the correct place: a fresh section is written exactly the way
    Godot writes it (``[editor_plugins]`` immediately followed by ``enabled=``,
    no blank line in between).
    """
    lines = text.splitlines(keepends=True)
    newline = detect_newline(text)
    header, end = section_bounds(lines, PLUGINS_SECTION)

    if header >= 0:
        for index in range(header + 1, end):
            line = lines[index]
            stripped = line.rstrip("\r\n")
            if not _ENABLED_LINE_RE.match(stripped):
                continue
            ending = line[len(stripped) :]
            indent = line[: len(line) - len(line.lstrip())]
            lines[index] = "%senabled=%s%s" % (indent, format_packed(items), ending)
            return "".join(lines)

    rendered = "enabled=" + format_packed(items) + newline
    if header >= 0:
        if not lines[header].endswith(("\n", "\r")):
            lines[header] = lines[header] + newline
        insert_at = header + 1
        block: List[str] = []
        if insert_at < len(lines) and lines[insert_at].strip():
            block.append(newline)
        block.append(rendered)
        lines[insert_at:insert_at] = block
        return "".join(lines)

    body = text
    if body and not body.endswith(("\n", "\r")):
        body += newline
    if body.strip():
        body += newline
    # Godot writes the section header followed directly by its keys, so do the
    # same here instead of inserting a cosmetic (and non canonical) blank line.
    body += "[" + PLUGINS_SECTION + "]" + newline + rendered
    return body


def guess_version(text: str) -> str:
    """Best effort Godot version hint taken from ``config/features``."""
    match = re.search(r"config/features\s*=\s*PackedStringArray\s*\(([^)]*)\)", text)
    if match:
        items = parse_packed_items(match.group(1))
        if items:
            return items[0]
    match = re.search(r"^config_version\s*=\s*(\d+)", text, re.MULTILINE)
    if match:
        return "config_version=%s" % match.group(1)
    return ""


def project_name(text: str, fallback: str) -> str:
    """Read ``config/name`` from ``project.godot``."""
    match = re.search(r'^config/name\s*=\s*"([^"]*)"', text, re.MULTILINE)
    if match and match.group(1).strip():
        return match.group(1)
    return fallback


def config_version(text: str, default: int = 0) -> int:
    """Return the ``config_version`` header value of ``project.godot``.

    Args:
        text: Full contents of ``project.godot``.
        default: Value returned when the key is missing or unparsable.
    Returns:
        The integer config version, or ``default``.
    """
    match = _CONFIG_VERSION_RE.search(text)
    if not match:
        return default
    try:
        return int(match.group(1))
    except (TypeError, ValueError):  # pragma: no cover - regex already limits it
        return default


__all__ = [
    "PLUGINS_SECTION",
    "PLUGIN_RES_PATH",
    "PROJECT_BACKUP",
    "PROJECT_FILE",
    "config_version",
    "detect_newline",
    "editor_plugins_enabled",
    "format_packed",
    "guess_version",
    "parse_packed_items",
    "project_name",
    "read_text",
    "section_bounds",
    "set_enabled_items",
    "write_text",
]
