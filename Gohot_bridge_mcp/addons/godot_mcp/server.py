#!/usr/bin/env python3
"""Compatibility shim: keeps the v1 plugin entry point working.

Existing MCP client configs point at ``addons/godot_mcp/server.py``. This file
just puts the repository root on ``sys.path`` and delegates to the real package
``server.server`` (protocol 2.0, SDK 1.x/2.x compatible).
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from server.server import main  # noqa: E402  (path setup must run first)

if __name__ == "__main__":
    sys.exit(main())
