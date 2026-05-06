"""
gedcom_theme.py

Theme constants, system dark-mode detection, and the Tooltip widget helper.
"""

import subprocess
import sys
import tkinter as tk


def _detect_system_theme():
    """Return 'Dark' or 'Light' based on the OS dark-mode setting."""
    if sys.platform == 'darwin':
        try:
            result = subprocess.run(
                ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                capture_output=True, text=True,
            )
            return 'Dark' if result.stdout.strip().lower() == 'dark' else 'Light'
        except Exception:
            return 'Light'
    elif sys.platform == 'win32':
        try:
            import winreg # pylint: disable=import-outside-toplevel
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
            )
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            winreg.CloseKey(key)
            return 'Light' if value == 1 else 'Dark'
        except Exception:
            return 'Light'
    else:
        # Linux / other: try the freedesktop color-scheme preference
        try:
            result = subprocess.run(
                ['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'],
                capture_output=True, text=True,
            )
            return 'Dark' if 'dark' in result.stdout.lower() else 'Light'
        except Exception: # pylint: disable=broad-exception-caught
            return 'Light'


class Tooltip:
    """Small hover tooltip attached to a Tkinter widget."""

    def __init__(self, widget, text):
        """Bind tooltip display behavior to widget hover events."""
        self._widget = widget
        self._text = text
        self._tip = None
        widget.bind('<Enter>', self._show)
        widget.bind('<Leave>', self._hide)

    def _show(self, _event=None):
        """Create and position the tooltip window."""
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f'+{x}+{y}')
        tk.Label(
            self._tip, text=self._text, justify='left',
            background='#ffffe0', relief='solid', borderwidth=1,
            wraplength=360, padx=4, pady=2,
        ).pack()

    def _hide(self, _event=None):
        """Destroy the tooltip window if it is visible."""
        if self._tip:
            self._tip.destroy()
            self._tip = None


THEME_NAMES = ('System', 'Light', 'Dark', 'Blue', 'Green')

THEMES = {
    'Light': {
        'ttk': 'clam',
        'bg': '#f0f2f5', 'fg': '#1a1a1a',
        'button_bg': '#dde1e7', 'field_bg': '#ffffff',
        'text_bg': '#ffffff', 'text_fg': '#1a1a1a',
        'select_bg': '#3d7ec7', 'select_fg': '#ffffff',
        'heading_bg': '#d0d4db', 'trough': '#c5c9d0',
        'flag_bg': '#fff4cc', 'link': '#1155bb', 'insert': '#1a1a1a',
    },
    'Dark': {
        'ttk': 'clam',
        'bg': '#2b2b2b', 'fg': '#d4d4d4',
        'button_bg': '#404040', 'field_bg': '#3c3c3c',
        'text_bg': '#1e1e1e', 'text_fg': '#d4d4d4',
        'select_bg': '#264f78', 'select_fg': '#ffffff',
        'heading_bg': '#404040', 'trough': '#1e1e1e',
        'flag_bg': '#3d3000', 'link': '#6bbfff', 'insert': '#d4d4d4',
    },
    'Blue': {
        'ttk': 'clam',
        'bg': '#d4e4f5', 'fg': '#0a2040',
        'button_bg': '#b8d0e8', 'field_bg': '#eaf2fb',
        'text_bg': '#eaf2fb', 'text_fg': '#0a2040',
        'select_bg': '#1a5c9a', 'select_fg': '#ffffff',
        'heading_bg': '#b0c8e0', 'trough': '#a8c0d8',
        'flag_bg': '#fffacc', 'link': '#004499', 'insert': '#0a2040',
    },
    'Green': {
        'ttk': 'clam',
        'bg': '#d0ebd0', 'fg': '#0a2a0a',
        'button_bg': '#b8d8b8', 'field_bg': '#e8f5e8',
        'text_bg': '#e8f5e8', 'text_fg': '#0a2a0a',
        'select_bg': '#2a6a2a', 'select_fg': '#ffffff',
        'heading_bg': '#a8c8a8', 'trough': '#a0c0a0',
        'flag_bg': '#fffacc', 'link': '#005500', 'insert': '#0a2a0a',
    },
}
