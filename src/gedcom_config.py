#!/usr/bin/env python3
"""
gedcom_config.py

Typed persistence layer for settings.json — no GUI imports.
"""

import json
import sys
from pathlib import Path


class ConfigManager:
    """Read/write a single settings.json file; all I/O is isolated here."""

    def __init__(self, config_path: Path):
        self._path = config_path

    # ------------------------------------------------------------------
    # Generic key/value accessors
    # ------------------------------------------------------------------

    def load_value(self, key, default=None):
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
            return data.get(key, default)
        except Exception:
            return default

    def save_value(self, key, value):
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
        except Exception:
            data = {}
        data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # ------------------------------------------------------------------
    # Typed accessors
    # ------------------------------------------------------------------

    def get_recent_files(self):
        raw = self.load_value('recent_files', [])
        return [p for p in raw if isinstance(p, str)]

    def set_recent_files(self, files):
        self.save_value('recent_files', files)

    def get_home_person(self, gedcom_path):
        return self.load_value('home_persons', {}).get(gedcom_path)

    def set_home_person(self, gedcom_path, indi_id):
        home_persons = self.load_value('home_persons', {})
        home_persons[gedcom_path] = indi_id
        self.save_value('home_persons', home_persons)

    def get_font_preference(self, valid_sizes):
        pref = self.load_value('font_size', 'medium')
        return pref if pref in valid_sizes else 'medium'

    def set_font_preference(self, size_name):
        self.save_value('font_size', size_name)

    def get_theme_preference(self, valid_themes):
        pref = self.load_value('theme', 'Default')
        return pref if pref in valid_themes else 'Default'

    def set_theme_preference(self, theme_name):
        self.save_value('theme', theme_name)

    def get_window_geometry(self, key):
        return self.load_value(key)

    def set_window_geometry(self, key, geometry):
        self.save_value(key, geometry)

    def get_top_n(self, default=3):
        val = self.load_value('top_n', default)
        try:
            return max(1, int(val))
        except (TypeError, ValueError):
            return default

    def set_top_n(self, value):
        self.save_value('top_n', int(value))

    def get_max_depth(self, default=50):
        val = self.load_value('max_depth', default)
        try:
            return max(1, int(val))
        except (TypeError, ValueError):
            return default

    def set_max_depth(self, value):
        self.save_value('max_depth', int(value))

    # ------------------------------------------------------------------
    # Platform default path
    # ------------------------------------------------------------------

    @staticmethod
    def default_path():
        if sys.platform == 'win32':
            import os
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "com.ajkessel.gedcom-dna-finder")
            base = Path(os.environ.get('APPDATA', Path.home()))
        elif sys.platform == 'darwin':
            base = Path.home() / 'Library' / 'Application Support'
        else:
            import os
            base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
        return base / 'gedcom-dna-finder' / 'settings.json'
