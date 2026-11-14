"""Platform bootstrap for the tkinter front-end.

Three jobs that are easy to keep apart from the widget code: making the window
sharp on Windows high-DPI screens, picking the best available ttk theme, and
making the primary action button actually *look* like the primary action.

Theme selection is a two stage process. The native themes (``vista`` /
``xpnative`` / ``aqua``) render buttons with operating system artwork and
silently ignore ``-background`` from ``ttk::style configure``, which would leave
the accent coloured "install" button as a plain grey one. So we first try the
nicest native theme and, when it cannot paint our colours, fall back to ``clam``
which honours every colour option we set.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Sequence, Tuple

#: Preferred themes, best looking first, colour capable one last.
THEME_PREFERENCE: Tuple[str, ...] = ("vista", "xpnative", "aqua", "clam")

#: Themes whose buttons are drawn by the OS: ``-background`` is ignored.
NATIVE_THEMES = frozenset(("vista", "xpnative", "winnative", "aqua"))

#: Themes known to paint the widget ``-background`` option on buttons.
COLOR_READY_THEMES = frozenset(("clam", "alt", "default", "classic"))

#: Accent palette of the primary action button.
ACTION_BACKGROUND = "#2563eb"
ACTION_BACKGROUND_ACTIVE = "#1d4ed8"
ACTION_BACKGROUND_DISABLED = "#93c5fd"
ACTION_FOREGROUND = "#ffffff"

#: Colour of the secondary buttons (uninstall / open folder).
SECONDARY_FOREGROUND = "#475569"
SECONDARY_FOREGROUND_ACTIVE = "#1e293b"
SECONDARY_FOREGROUND_DISABLED = "#94a3b8"

#: Base font size used everywhere in the window.
BASE_FONT_SIZE = 10

#: Style name of the progress bar; the GUI imports this constant so the widget
#: and the style database can never drift apart.
PROGRESS_STYLE = "Trough.TProgressbar"

#: Trough (empty track) colour: light enough to be quiet, dark enough to see.
PROGRESS_TROUGH_COLOR = "#e2e8f0"
#: One pixel border of the track, so the bar reads as a control at 0%.
PROGRESS_TRACK_BORDER = "#cbd5e1"
#: Fill colour of the bar (accent blue).
PROGRESS_BAR_COLOR = ACTION_BACKGROUND
#: Track height in pixels; must stay >= 10 so an empty bar is still obvious.
PROGRESS_THICKNESS = 12

#: Options accepted by ``ttk::style configure`` that we rely on.
_ACTION_PADDING = (18, 10)
_SECONDARY_PADDING = (10, 8)


def enable_high_dpi() -> None:
    """Opt into DPI awareness on Windows (best effort, never raises)."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:  # noqa: BLE001 - older Windows has no shcore
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - nothing else to try
        pass


def available_themes(root: tk.Misc) -> List[str]:
    """Return the ttk themes this Tk build offers.

    Args:
        root: Any widget of the interpreter (usually the toplevel).
    Returns:
        Theme names, in the order Tk reports them. Empty when the query fails.
    """
    try:
        names = root.tk.call("ttk::style", "theme", "names")
    except tk.TclError:  # pragma: no cover - ancient Tk builds
        return []
    if isinstance(names, str):  # a single theme comes back as a bare string
        return [names]
    try:
        return [str(name) for name in names]
    except TypeError:  # pragma: no cover - defensive
        return []


def button_paints_background(style: ttk.Style, theme: str) -> bool:
    """Report whether ``theme`` honours ``-background`` on ttk buttons.

    Native themes paint the button with platform artwork and ignore colours, so
    they are reported as ``False`` even though ``style.lookup`` echoes the value
    straight back (the setting is stored, it is just never drawn).

    Args:
        style: The style database of the running interpreter.
        theme: Name of the theme that is currently active.
    Returns:
        True when configuring a button background changes what the user sees.
    """
    if theme in NATIVE_THEMES:
        return False
    if theme in COLOR_READY_THEMES:
        return True
    # Unknown theme: inspect the outermost element of the button layout.
    try:
        layout = style.layout("TButton") or []
    except tk.TclError:  # pragma: no cover - theme without buttons
        return False
    for element in layout:
        if not isinstance(element, (list, tuple)) or not element:
            continue
        try:
            options = style.element_options(str(element[0]))
        except tk.TclError:  # pragma: no cover - element without options
            continue
        if "background" in tuple(options):
            return True
    return False


def _choose_theme(available: Sequence[str]) -> str:
    """Return the most preferred theme name that ``available`` contains."""
    for candidate in THEME_PREFERENCE:
        if candidate in available:
            return candidate
    return str(available[0]) if available else "default"


def _configure_base_fonts(style: ttk.Style) -> None:
    """Give every common widget the same readable font size."""
    names = (
        "TButton",
        "TLabel",
        "TEntry",
        "TCheckbutton",
        "TRadiobutton",
        "TLabelframe.Label",
        "TNotebook.Tab",
    )
    for name in names:
        try:
            style.configure(name, font=("", BASE_FONT_SIZE))
        except tk.TclError:  # pragma: no cover - theme without the option
            continue


def _configure_action_style(style: ttk.Style) -> None:
    """Make ``Action.TButton`` a filled, accent coloured primary button."""
    configure: Dict[str, Any] = {
        "font": ("", BASE_FONT_SIZE + 1, "bold"),
        "background": ACTION_BACKGROUND,
        "foreground": ACTION_FOREGROUND,
        "padding": _ACTION_PADDING,
        "borderwidth": 0,
        "relief": "flat",
        "anchor": "center",
    }
    for option, value in configure.items():
        try:
            style.configure("Action.TButton", **{option: value})
        except tk.TclError:  # pragma: no cover - theme without the option
            continue
    mapping: Dict[str, List[Tuple[str, Any]]] = {
        "background": [
            ("disabled", ACTION_BACKGROUND_DISABLED),
            ("pressed", ACTION_BACKGROUND_ACTIVE),
            ("active", ACTION_BACKGROUND_ACTIVE),
        ],
        "foreground": [
            ("disabled", ACTION_FOREGROUND),
            ("pressed", ACTION_FOREGROUND),
            ("active", ACTION_FOREGROUND),
        ],
        "relief": [("pressed", "flat"), ("active", "flat"), ("!disabled", "flat")],
    }
    for option, states in mapping.items():
        try:
            style.map("Action.TButton", **{option: states})
        except tk.TclError:  # pragma: no cover - theme without the option
            continue


def _configure_secondary_style(style: ttk.Style) -> None:
    """Make ``Secondary.TButton`` visually quieter than the primary action."""
    try:
        style.configure(
            "Secondary.TButton",
            font=("", BASE_FONT_SIZE),
            foreground=SECONDARY_FOREGROUND,
            padding=_SECONDARY_PADDING,
        )
    except tk.TclError:  # pragma: no cover - theme without the option
        pass
    try:
        style.map(
            "Secondary.TButton",
            foreground=[
                ("disabled", SECONDARY_FOREGROUND_DISABLED),
                ("pressed", SECONDARY_FOREGROUND_ACTIVE),
                ("active", SECONDARY_FOREGROUND_ACTIVE),
            ],
        )
    except tk.TclError:  # pragma: no cover - theme without the option
        pass


def _configure_progress_style(style: ttk.Style) -> None:
    """Give the progress bar a light, always visible trough.

    ``clam`` paints the ``pbar`` element only where the value reaches, so a bar
    at ``value=0`` would collapse into nothing at all unless the *trough*
    element is configured with a visible colour, a border and a usable
    thickness. Both the determinate (0 % / 100 %) and the indeterminate (busy)
    modes share this one style, so the track never changes appearance.

    Args:
        style: The style database of the running interpreter.
    """
    # An explicit layout keeps the trough element in place even for the custom
    # style name we derive (ttk would otherwise inherit whatever the current
    # theme happens to use).
    try:
        style.layout(
            PROGRESS_STYLE,
            [
                (
                    "Horizontal.Progressbar.trough",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Horizontal.Progressbar.pbar",
                                {"side": "left", "sticky": "ns"},
                            )
                        ],
                    },
                )
            ],
        )
    except tk.TclError:  # pragma: no cover - theme without progressbars
        pass

    options: Dict[str, Any] = {
        "background": PROGRESS_BAR_COLOR,
        "troughcolor": PROGRESS_TROUGH_COLOR,
        "bordercolor": PROGRESS_TRACK_BORDER,
        "lightcolor": PROGRESS_TROUGH_COLOR,
        "darkcolor": PROGRESS_TRACK_BORDER,
        "borderwidth": 1,
        "thickness": PROGRESS_THICKNESS,
    }
    for option, value in options.items():
        try:
            style.configure(PROGRESS_STYLE, **{option: value})
        except tk.TclError:  # pragma: no cover - theme without the option
            continue


def apply_theme(root: tk.Tk) -> str:
    """Pick the best ttk theme and install the installer's custom styles.

    The native theme is preferred for a platform look; when it cannot render the
    accent colours the window switches to ``clam`` so the primary button stays
    blue instead of grey.

    Args:
        root: The toplevel window.
    Returns:
        The name of the theme that ended up being used.
    """
    style = ttk.Style(root)
    available = available_themes(root)
    chosen = _choose_theme(available)

    theme_used = chosen
    try:
        style.theme_use(chosen)
    except tk.TclError:  # pragma: no cover - theme vanished mid-flight
        try:
            theme_used = str(style.theme_use())
        except tk.TclError:
            theme_used = chosen

    _configure_base_fonts(style)
    _configure_action_style(style)
    _configure_secondary_style(style)
    _configure_progress_style(style)

    if not button_paints_background(style, theme_used) and "clam" in available:
        try:
            style.theme_use("clam")
            theme_used = "clam"
            # Style settings live in a per-theme database, so rebuild them.
            _configure_base_fonts(style)
            _configure_action_style(style)
            _configure_secondary_style(style)
            _configure_progress_style(style)
        except tk.TclError:  # pragma: no cover - defensive
            pass

    return theme_used


__all__ = [
    "ACTION_BACKGROUND",
    "ACTION_BACKGROUND_ACTIVE",
    "ACTION_BACKGROUND_DISABLED",
    "ACTION_FOREGROUND",
    "BASE_FONT_SIZE",
    "NATIVE_THEMES",
    "PROGRESS_BAR_COLOR",
    "PROGRESS_STYLE",
    "PROGRESS_THICKNESS",
    "PROGRESS_TRACK_BORDER",
    "PROGRESS_TROUGH_COLOR",
    "SECONDARY_FOREGROUND",
    "THEME_PREFERENCE",
    "apply_theme",
    "available_themes",
    "button_paints_background",
    "enable_high_dpi",
]
