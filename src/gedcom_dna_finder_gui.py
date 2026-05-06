#!/usr/bin/env python3
"""
gedcom_dna_finder_gui.py

Tkinter GUI for finding the nearest DNA-flagged relative(s) to a target
person in a GEDCOM tree.

Workflow:
  1. Browse to your GEDCOM and click Load.
  2. Type in the search box to filter the people list.
  3. Select a person and click "Find Nearest DNA Matches"
     (or just double-click the row).
  4. The right pane shows the path from that person to the nearest
     DNA-flagged relative(s).

Two DNA-flag signals are detected (either is sufficient):
  - A source-citation PAGE line whose text contains "AncestryDNA Match"
  - An _MTTAG pointer to a tag-record whose NAME contains "DNA"
    (configurable from the UI)

Pure stdlib. Requires Python 3 with tkinter (standard on Windows / macOS;
on Linux you may need a python3-tk package).
"""

import tkinter.font as tkfont
import argparse
import difflib
import os
import re
import subprocess
import sys
import threading
import webbrowser
from tkinter import ttk, filedialog, messagebox, scrolledtext
import tkinter as tk
from gedcom_data_model import GedcomDataModel
from gedcom_config import ConfigManager
from gedcom_strings import *  # user-facing strings noqa: F401,F403 # pylint: disable=unused-wildcard-import,wildcard-import
from gedcom_core import (
    bfs_find_dna_matches,
    bfs_find_all_paths,
    describe,
    extract_ged_from_zip,
)
from gedcom_relationship import (
    get_ancestor_depths,
    get_descendant_depths,
    describe_relationship,
)
from gedcom_theme import Tooltip, THEME_NAMES, THEMES
from gedcom_gui_appearance import AppearanceMixin
from gedcom_gui_dialogs import DialogsMixin


def _open_url(url):
    # webbrowser.open() silently fails in PyInstaller .app bundles on macOS
    # because Python routes through osascript, which can break in frozen apps.
    # /usr/bin/open is always available and handles URLs reliably.
    if sys.platform == 'darwin':
        subprocess.run(['/usr/bin/open', url], check=False)
    else:
        webbrowser.open(url)


def _read_version():
    _bases = []
    if getattr(sys, 'frozen', False):
        _bases.append(sys._MEIPASS)
    _bases.append(os.path.join(os.path.dirname(
        os.path.abspath(__file__)), '..'))
    for _base in _bases:
        _path = os.path.join(_base, 'gedcom_dna_finder', '__init__.py')
        if os.path.isfile(_path):
            with open(_path, encoding='utf-8') as f:
                _src = f.read()
            _v = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _src)
            _d = re.search(r'__release_date__\s*=\s*["\']([^"\']+)["\']', _src)
            if _v and _d:
                return _v.group(1), _d.group(1)
    return 'unknown', 'unknown'


__version__, __release_date__ = _read_version()


# ===========================================================================
# GUI
# ===========================================================================

class DNAMatchFinderApp(DialogsMixin, AppearanceMixin):
    """Tkinter application for browsing GEDCOM people and finding DNA matches."""

    MAX_LIST_DISPLAY = 2000  # cap visible rows in the people list
    FUZZY_THRESHOLD = 0.72   # minimum SequenceMatcher ratio to count as a match
    MAX_RECENT = 10          # number of recent files to remember
    _FONT_SIZES = {
        'small':  {'ui': 9,  'mono': 9},
        'medium': {'ui': 10, 'mono': 10},
        'large':  {'ui': 13, 'mono': 12},
    }
    _THEME_NAMES = THEME_NAMES
    _THEMES = THEMES

    def __init__(self, root):
        """Initialize application state, preferences, data model, and widgets."""
        self._config = ConfigManager(ConfigManager.default_path())
        self._model = GedcomDataModel()

        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1100x720")
        self.root.minsize(800, 500)
        if sys.platform == 'win32':
            self.root.iconbitmap(self._resource_path('icons/family_tree.ico'))
        elif sys.platform != 'darwin':
            icon = tk.PhotoImage(
                file=self._resource_path('icons/family_tree.png'))
            self.root.iconphoto(True, icon)
        # macOS: icon is handled by the .app bundle's .icns file

        # Data state
        self.individuals = {}
        self.families = {}
        self.tag_records = {}
        # all IDs sorted by name (computed once after load)
        self.sorted_ids = []
        self._home_person_id = None   # persisted per GEDCOM file

        # UI state
        self.gedcom_path = tk.StringVar()
        self.tag_keyword = tk.StringVar(value="DNA")
        self.page_marker = tk.StringVar(value="AncestryDNA Match")
        self.search_text = tk.StringVar()
        self.filter_text = tk.StringVar()
        self.show_flagged_only = tk.BooleanVar(value=False)
        self.top_n = tk.IntVar(value=self._config.get_top_n())
        self.max_depth = tk.IntVar(value=self._config.get_max_depth())
        self.fuzzy_threshold = tk.DoubleVar(
            value=self._config.get_fuzzy_threshold(self.FUZZY_THRESHOLD))
        self.status_text = tk.StringVar(value=STATUS_NO_FILE)

        self.fuzzy_search = tk.BooleanVar(value=False)
        self.show_ids = tk.BooleanVar(value=self._config.get_show_ids())
        self._name_order = self._config.get_name_order()

        self.search_text.trace_add('write', self._on_search_change)
        self.filter_text.trace_add('write', self._on_search_change)
        self.show_flagged_only.trace_add('write', self._on_search_change)
        self.fuzzy_search.trace_add('write', self._on_search_change)
        self._search_after_id = None

        self.top_n.trace_add('write', self._on_settings_change)
        self.max_depth.trace_add('write', self._on_settings_change)
        self.fuzzy_threshold.trace_add('write', self._on_search_change)
        self._settings_after_id = None

        self.tag_keyword.trace_add('write', self._on_dna_settings_change)
        self.page_marker.trace_add('write', self._on_dna_settings_change)
        self._dna_settings_after_id = None
        # {'type': 'dna_matches'|'path', 'start_id': ..., 'end_id': ...}
        self._last_result = None
        self._busy = False
        self._sort_col = 'name'
        self._sort_rev = False

        self._recent_files = self._load_history()
        self._show_person_geometry = self._load_show_person_geometry()

        self._default_ttk_theme = ttk.Style().theme_use()
        self._mono_font = tkfont.Font(family='Courier', size=10)
        self._mono_font_bold = tkfont.Font(
            family='Courier', size=10, weight='bold')
        self._link_color = '#0066cc'
        self._font_size_pref = self._load_font_preference()
        self._theme_pref = self._load_theme_preference()
        self._apply_font_size(self._font_size_pref)
        self._apply_theme(self._theme_pref)

        self._version = __version__
        self._release_date = __release_date__

        # Remove any leftover .pkl files from the old pickle-based cache
        try:
            for _pkl in self._cache_dir().glob('*.pkl'):
                _pkl.unlink(missing_ok=True)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        self._build_ui()

        # Snap the initial window width up to what Tk actually needs so that
        # all action-bar buttons are fully visible on first launch.
        # update_idletasks() is a best-effort pass; geometry propagation in Tk
        # can require multiple idle rounds, so we also schedule a deferred
        # refit that runs after the event loop starts and layout has settled.
        # This is necessary when a non-default font (e.g. "large") is configured
        # at startup, because a single update_idletasks() call may return a
        # stale winfo_reqwidth() before all geometry passes have completed.
        self.root.update_idletasks()
        min_w = self.root.winfo_reqwidth()
        if min_w > self.root.winfo_width():
            self.root.geometry(f"{min_w}x{self.root.winfo_height()}")
        self.root.minsize(max(min_w, 800), 500)
        self.root.after(0, self._refit_windows)

        # Re-open the most-recently-used file automatically on startup
        if self._recent_files and os.path.isfile(self._recent_files[0]):
            self.gedcom_path.set(self._recent_files[0])
            self.root.after(0, self._load_file)

    # ---------------------------------------------------------- UI build
    def _build_ui(self):
        """Build the main application window and connect primary controls."""
        self._setup_menu()
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill='both', expand=True)

        # File row
        file_frame = ttk.LabelFrame(outer, text=FRAME_GEDCOM_FILE, padding=8)
        file_frame.pack(fill='x')
        self.path_combo = ttk.Combobox(
            file_frame, textvariable=self.gedcom_path, values=self._recent_files
        )
        self.path_combo.pack(side='left', fill='x', expand=True, padx=(0, 4))
        self.path_combo.bind('<<ComboboxSelected>>',
                             lambda _: self._load_file())
        self.browse_btn = ttk.Button(file_frame, text=BTN_BROWSE,
                                     command=self._browse)
        self.browse_btn.pack(side='left', padx=2)
        Tooltip(self.browse_btn, TIP_BROWSE)

        # Settings row
        settings_frame = ttk.LabelFrame(
            outer, text=FRAME_DNA_SETTINGS, padding=8)
        settings_frame.pack(fill='x', pady=(8, 0))
        ttk.Label(settings_frame, text=LBL_TAG_KEYWORD).grid(
            row=0, column=0, sticky='w', padx=(0, 4))
        ttk.Entry(settings_frame, textvariable=self.tag_keyword,
                  width=20).grid(row=0, column=1, padx=(0, 16))
        ttk.Label(settings_frame, text=LBL_PAGE_MARKER).grid(
            row=0, column=2, sticky='w', padx=(0, 4))
        ttk.Entry(settings_frame, textvariable=self.page_marker,
                  width=30).grid(row=0, column=3, padx=(0, 16))
        _select_tag_btn = ttk.Button(settings_frame, text=BTN_SELECT_TAG,
                                     underline=7, command=self._view_tags)
        _select_tag_btn.grid(row=0, column=4, padx=4)
        Tooltip(_select_tag_btn, TIP_SELECT_TAG)
        _find_path_btn = ttk.Button(settings_frame, text=BTN_FIND_PATH,
                                    underline=18, command=self._find_path)
        _find_path_btn.grid(row=0, column=5, padx=(12, 4))
        Tooltip(_find_path_btn, TIP_FIND_PATH)

        # Main paned area
        paned = ttk.PanedWindow(outer, orient='horizontal')
        paned.pack(fill='both', expand=True, pady=(8, 0))

        # --- Left pane: search + list + action controls ---
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        search_frame = ttk.Frame(left)
        search_frame.pack(fill='x')
        ttk.Label(search_frame, text=LBL_FIND, underline=0).pack(
            side='left', padx=(0, 4))
        self.search_entry = ttk.Entry(
            search_frame, textvariable=self.search_text)
        self.search_entry.pack(side='left', fill='x', expand=True)
        self.search_entry.bind(
            '<Return>', lambda _: self._search_flush_and_jump())
        ttk.Checkbutton(
            search_frame, text=CHK_DNA_FLAGGED_ONLY, underline=0, variable=self.show_flagged_only
        ).pack(side='left', padx=(8, 0))
        ttk.Checkbutton(
            search_frame, text=CHK_FUZZY, variable=self.fuzzy_search, underline=1
        ).pack(side='left', padx=(8, 0))

        filter_frame = ttk.Frame(left)
        filter_frame.pack(fill='x', pady=(2, 0))
        ttk.Label(filter_frame, text=LBL_FILTER, underline=1).pack(
            side='left', padx=(0, 4))
        self.filter_entry = ttk.Entry(
            filter_frame, textvariable=self.filter_text)
        self.filter_entry.pack(side='left', fill='x', expand=True)
        self.filter_entry.bind('<Return>', lambda _: self._kb_focus_list())

        list_frame = ttk.Frame(left)
        list_frame.pack(fill='both', expand=True, pady=(4, 0))

        self.tree = ttk.Treeview(
            list_frame,
            columns=('name', 'birth', 'death', 'flagged'),
            show='headings',
            selectmode='browse',
        )
        self.tree.heading('name', text=COL_NAME,
                          command=lambda: self._sort_by('name'))
        self.tree.heading('birth', text=COL_BIRTH,
                          command=lambda: self._sort_by('birth'))
        self.tree.heading('death', text=COL_DEATH,
                          command=lambda: self._sort_by('death'))
        self.tree.heading('flagged', text=COL_DNA,
                          command=lambda: self._sort_by('flagged'))
        self.tree.column('name', width=240, anchor='w', stretch=True)
        self.tree.column('birth', width=55, anchor='w', stretch=False)
        self.tree.column('death', width=55, anchor='w', stretch=False)
        self.tree.column('flagged', width=50, anchor='center', stretch=False)

        ysb = ttk.Scrollbar(list_frame, orient='vertical',
                            command=self.tree.yview)
        ysb.configure(takefocus=False)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        ysb.pack(side='right', fill='y')

        # Highlight flagged rows
        self.tree.tag_configure('flagged_row', background='#fff4cc')

        self.tree.bind('<Double-1>', lambda e: self._find_matches())
        self.tree.bind('<Return>', lambda e: self._find_matches())
        self.tree.bind('<Key>', self._tree_type_ahead)
        self.tree.bind('<Home>', lambda e: self._tree_jump('first') or 'break')
        self.tree.bind('<End>', lambda e: self._tree_jump('last') or 'break')

        # Action controls
        action_frame = ttk.Frame(left)
        action_frame.pack(fill='x', pady=(6, 0))
        _top_n_label = ttk.Label(action_frame, text=LBL_TOP_N)
        _top_n_label.pack(side='left')
        self.top_n_spin = ttk.Spinbox(
            action_frame, from_=1, to=20, textvariable=self.top_n, width=4)
        self.top_n_spin.pack(side='left', padx=(2, 12))
        Tooltip(_top_n_label, TIP_TOP_N)
        Tooltip(self.top_n_spin, TIP_TOP_N)
        _max_depth_label = ttk.Label(action_frame, text=LBL_MAX_DEPTH)
        _max_depth_label.pack(side='left')
        self.max_depth_spin = ttk.Spinbox(
            action_frame, from_=1, to=200, textvariable=self.max_depth, width=4)
        self.max_depth_spin.pack(side='left', padx=(2, 12))
        Tooltip(_max_depth_label, TIP_MAX_DEPTH)
        Tooltip(self.max_depth_spin, TIP_MAX_DEPTH)
        self.find_matches_btn = ttk.Button(
            action_frame, text=BTN_FIND_MATCHES, underline=5,
            command=self._find_matches
        )
        self.find_matches_btn.pack(side='right')
        self.show_person_btn = ttk.Button(
            action_frame, text=BTN_SHOW_PERSON, underline=0,
            command=self._show_person
        )
        self.show_person_btn.pack(side='right', padx=(0, 6))
        self.set_home_btn = ttk.Button(
            action_frame, text=BTN_SET_HOME, underline=4,
            command=self._set_home_person
        )
        self.set_home_btn.pack(side='right', padx=(0, 4))

        # --- Right pane: results ---
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        results_header = ttk.Frame(right)
        results_header.pack(fill='x')
        ttk.Label(results_header, text=LBL_RESULTS).pack(side='left')
        ttk.Button(results_header, text=BTN_COPY, underline=0,
                   command=self._copy_results).pack(side='right')
        ttk.Button(results_header, text=BTN_CLEAR, underline=1,
                   command=self._clear_results).pack(side='right', padx=(0, 4))

        self.results = scrolledtext.ScrolledText(
            right, font=self._mono_font, wrap='word', height=10
        )
        self.results.pack(fill='both', expand=True, pady=(4, 0))
        self.results.tag_configure('bold', font=self._mono_font_bold)
        self.results.configure(state='disabled')

        # Status bar
        status_bar = ttk.Frame(outer, relief='sunken')
        status_bar.pack(fill='x', pady=(8, 0))
        status_bar.columnconfigure(0, weight=1)
        ttk.Label(status_bar, textvariable=self.status_text, anchor='w').grid(
            row=0, column=0, sticky='ew', padx=(4, 0), pady=1)
        self._progress_bar = ttk.Progressbar(
            status_bar, mode='indeterminate', length=130)
        self._progress_bar.grid(row=0, column=1, padx=(4, 2), pady=2)
        self._progress_bar.grid_remove()  # hidden until a long operation starts

        self._setup_keybindings()

    # ---------------------------------------------------------- Busy / progress
    def _show_progress(self, msg=None):
        """Reveal the indeterminate progress bar and optionally set status text."""
        if msg:
            self.status_text.set(msg)
        self._progress_bar.grid()
        self._progress_bar.start(12)
        self.root.update_idletasks()

    def _hide_progress(self):
        """Stop and hide the progress bar."""
        self._progress_bar.stop()
        self._progress_bar.grid_remove()

    def _set_busy(self, busy):
        """Disable or re-enable the controls that trigger long operations."""
        self._busy = busy
        state = 'disabled' if busy else 'normal'
        for widget in (self.browse_btn, self.path_combo, self.find_matches_btn):
            widget.configure(state=state)

    # ---------------------------------------------------------- Handlers
    def _browse(self):
        """Prompt for a GEDCOM or ZIP file and load it when selected."""
        current = self.gedcom_path.get().strip()
        initialdir = os.path.dirname(current) if current else None
        path = filedialog.askopenfilename(
            title=DLG_SELECT_GEDCOM,
            filetypes=[("GEDCOM files", "*.ged *.gedcom *.zip"),
                       ("All files", "*.*")],
            initialdir=initialdir,
        )
        if path:
            self.gedcom_path.set(path)
            self._load_file()

    def _load_file(self):
        """Load the selected GEDCOM file into the model and refresh the UI."""
        if self._busy:
            return
        path = self.gedcom_path.get().strip()
        if not path:
            messagebox.showerror(ERR_NO_FILE_TITLE, ERR_NO_FILE_MSG)
            return
        if not os.path.isfile(path):
            messagebox.showerror(ERR_NOT_FOUND_TITLE,
                                 ERR_NOT_FOUND_MSG.format(path=path))
            return

        gedcom_path = path
        tmp_path = None
        if path.lower().endswith('.zip'):
            try:
                tmp_path, ged_name = extract_ged_from_zip(path)
                gedcom_path = tmp_path
                self.status_text.set(
                    STATUS_EXTRACTED_ZIP.format(name=ged_name))
            except Exception as e:  # pylint: disable=broad-exception-caught
                messagebox.showerror(
                    ERR_ZIP_TITLE, ERR_ZIP_MSG.format(error=e))
                return

        self._show_progress(STATUS_LOADING)
        self._set_busy(True)

        dna_keyword = self.tag_keyword.get()
        page_marker = self.page_marker.get()
        cache_dir = self._cache_dir()

        def _do_load():
            try:
                result = self._model.load(
                    gedcom_path,
                    dna_keyword=dna_keyword,
                    page_marker=page_marker,
                    cache_dir=cache_dir,
                )
                self.root.after(0, lambda: _on_done(result, None))
            except Exception as e:  # pylint: disable=broad-exception-caught
                self.root.after(0, lambda: _on_done(None, e))

        def _on_done(result, error):
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            self._hide_progress()
            self._set_busy(False)
            if error:
                self.status_text.set(STATUS_LOAD_FAILED)
                messagebox.showerror(
                    ERR_PARSE_TITLE, ERR_PARSE_MSG.format(error=error))
                return
            from_cache, encoding_warning = result
            self.individuals = self._model.individuals
            self.families = self._model.families
            self.tag_records = self._model.tag_records
            if encoding_warning:
                messagebox.showwarning(ERR_ENCODING_TITLE, encoding_warning)
            self.sorted_ids = sorted(
                self.individuals.keys(),
                key=lambda iid: (self.individuals[iid]['name'].lower(), iid),
            )
            self._add_to_history(path)
            self._home_person_id = self._load_home_person(path)
            self._populate_tree()
            status = (STATUS_LOADED_CACHED.format(count=len(self.individuals))
                      if from_cache
                      else STATUS_LOADED.format(count=len(self.individuals)))
            self.status_text.set(status)

        threading.Thread(target=_do_load, daemon=True).start()

    def _on_settings_change(self, *_):
        """Debounce search depth and result count changes before rerendering."""
        if self._settings_after_id is not None:
            self.root.after_cancel(self._settings_after_id)
        self._settings_after_id = self.root.after(400, self._refresh_result)

    def _on_dna_settings_change(self, *_):
        """Debounce DNA marker setting changes before reloading data."""
        if self._dna_settings_after_id is not None:
            self.root.after_cancel(self._dna_settings_after_id)
        self._dna_settings_after_id = self.root.after(
            800, self._reload_if_loaded)

    def _reload_if_loaded(self):
        """Reload the active GEDCOM when DNA marker settings change."""
        self._dna_settings_after_id = None
        if self.individuals:
            self._load_file()

    def _refresh_result(self):
        """Recompute and redraw the most recent result view."""
        self._settings_after_id = None
        if not self._last_result or not self.individuals:
            return
        try:
            top_n = int(self.top_n.get())
            max_depth = int(self.max_depth.get())
        except (tk.TclError, ValueError):
            return
        kind = self._last_result['type']
        start_id = self._last_result['start_id']
        if kind == 'dna_matches':
            results = self._model.find_dna_matches(start_id, top_n, max_depth)
            self._render_results(start_id, results)
        elif kind == 'path':
            end_id = self._last_result['end_id']
            paths, truncated = self._model.find_all_paths(
                start_id, end_id, top_n, max_depth)
            self._render_path_results(start_id, end_id, paths, truncated)

    def _on_search_change(self, *_):
        """Debounce person-list filtering while the user types."""
        # Debounce so typing doesn't refilter on every keystroke
        if self._search_after_id is not None:
            self.root.after_cancel(self._search_after_id)
        self._search_after_id = self.root.after(150, self._populate_tree)

    def _search_flush_and_jump(self):
        """Apply pending search filters immediately and select the first row."""
        if self._search_after_id is not None:
            self.root.after_cancel(self._search_after_id)
            self._search_after_id = None
            self._populate_tree()
        self._tree_jump('first')

    def _populate_tree(self):
        """Populate the people list using current search, filter, and sort settings."""
        self._search_after_id = None
        # Save current selection so we can restore it if still visible
        prev_sel = self.tree.selection()
        prev_id = prev_sel[0] if prev_sel else None

        self.tree.delete(*self.tree.get_children())

        if not self.individuals:
            return

        query = self.search_text.get().strip().lower()
        query_tokens = query.split()
        filter_query = self.filter_text.get().strip().lower()
        flagged_only = self.show_flagged_only.get()
        flagged_count = sum(
            1 for i in self.individuals.values() if i['dna_markers'])

        # Update column heading sort indicators
        _col_labels = {'name': COL_NAME, 'birth': COL_BIRTH,
                       'death': COL_DEATH, 'flagged': COL_DNA}
        for _col, _label in _col_labels.items():
            suffix = (
                ' ▼' if self._sort_rev else ' ▲') if _col == self._sort_col else ''
            self.tree.heading(_col, text=_label + suffix)

        # Sort ids according to current sort column/direction
        def _sort_key(indi_id):
            indi = self.individuals[indi_id]
            name = self._display_name(indi).lower()
            if self._sort_col == 'birth':
                by = indi['birth_year']
                return (by is None, by or 0, name)
            if self._sort_col == 'death':
                dy = indi['death_year']
                return (dy is None, dy or 0, name)
            if self._sort_col == 'flagged':
                return (not bool(indi['dna_markers']), name)
            return (name, indi_id)

        display_ids = sorted(self.sorted_ids, key=_sort_key,
                             reverse=self._sort_rev)

        shown = 0
        truncated = False
        for indi_id in display_ids:
            indi = self.individuals[indi_id]
            if flagged_only and not indi['dna_markers']:
                continue
            if query_tokens:
                all_names = indi['alt_names'] or [indi['name']]
                id_lower = indi_id.lower()
                if self.fuzzy_search.get():
                    match = (
                        any(
                            all(self._fuzzy_token_matches(tok, name.lower().split())
                                for tok in query_tokens)
                            for name in all_names
                        )
                        or query in id_lower
                    )
                else:
                    match = (
                        any(
                            all(tok in name.lower() for tok in query_tokens)
                            for name in all_names
                        )
                        or query in id_lower
                    )
                if not match:
                    continue
            if filter_query:
                raw_text = ' '.join(v.lower() for _, _, _, v in indi['_raw'])
                if filter_query not in raw_text:
                    continue
            if shown >= self.MAX_LIST_DISPLAY:
                truncated = True
                break
            tags = ('flagged_row',) if indi['dna_markers'] else ()
            flagged_mark = '✓' if indi['dna_markers'] else ''
            self.tree.insert(
                '', 'end', iid=indi_id,
                values=(self._display_name(indi),
                        indi['birth_year'] or '',
                        indi['death_year'] or '',
                        flagged_mark),
                tags=tags,
            )
            shown += 1

        # Restore selection if still present; auto-select if exactly one result
        if prev_id and self.tree.exists(prev_id):
            self.tree.selection_set(prev_id)
            self.tree.see(prev_id)
        elif shown == 1 and (query or flagged_only):
            only = self.tree.get_children()[0]
            self.tree.selection_set(only)
            self.tree.see(only)

        # Status
        total = len(self.individuals)
        if truncated:
            self.status_text.set(STATUS_SHOWING_FIRST.format(
                max_display=self.MAX_LIST_DISPLAY, total=total, flagged=flagged_count))
        elif query or flagged_only:
            self.status_text.set(STATUS_MATCHES.format(
                shown=shown, plural='es' if shown != 1 else '',
                total=total, flagged=flagged_count))
        else:
            self.status_text.set(STATUS_OVERVIEW.format(
                total=total, families=len(self.families), flagged=flagged_count))

    def _fuzzy_token_matches(self, token, name_words):
        """Return whether token fuzzily matches any word in a name."""
        try:
            threshold = float(self.fuzzy_threshold.get())
        except (tk.TclError, ValueError):
            threshold = self.FUZZY_THRESHOLD
        return any(
            difflib.SequenceMatcher(
                None, token, word).ratio() >= threshold
            for word in name_words
        )

    def _sort_by(self, col):
        """Toggle sorting for the people list by the requested column."""
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self._populate_tree()

    def _find_matches(self):
        """Find and display nearest DNA-flagged matches for the selected person."""
        if self._busy:
            return
        if not self.individuals:
            messagebox.showwarning(ERR_NO_DATA_TITLE, ERR_NO_DATA_MSG)
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(ERR_NO_SEL_TITLE, ERR_NO_SEL_MSG)
            return
        start_id = sel[0]
        try:
            top_n = int(self.top_n.get())
            max_depth = int(self.max_depth.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(ERR_BAD_VAL_TITLE, ERR_BAD_VAL_TOP_N)
            return

        self._show_progress()
        self._set_busy(True)

        def _do_search():
            try:
                results = self._model.find_dna_matches(
                    start_id, top_n, max_depth)
                self.root.after(0, lambda: _on_done(results, None))
            except Exception as e:  # pylint: disable=broad-exception-caught
                self.root.after(0, lambda: _on_done(None, e))

        def _on_done(results, error):
            self._hide_progress()
            self._set_busy(False)
            if error:
                messagebox.showerror(ERR_PARSE_TITLE, str(error))
                return
            self._last_result = {'type': 'dna_matches', 'start_id': start_id}
            self._render_results(start_id, results)

        threading.Thread(target=_do_search, daemon=True).start()

    def _render_results(self, start_id, results):
        """Render DNA match search results and family context."""
        w = self.results
        w.configure(state='normal')
        w.delete('1.0', 'end')
        self._clear_person_tags(w)

        w.tag_configure('person_link')
        w.tag_bind('person_link', '<Enter>',
                   lambda _: w.config(cursor='hand2'))
        w.tag_bind('person_link', '<Leave>', lambda _: w.config(cursor=''))

        def nl(text='', bold=False):
            w.insert('end', text + '\n', ('bold',) if bold else ())

        def hr():
            w.update_idletasks()
            pix_width = max(w.winfo_width() - 4, 100)
            sep = tk.Frame(w, height=2, width=pix_width, bg=w.cget('fg'), bd=0, relief='flat')
            w.window_create('end', window=sep)
            w.insert('end', '\n')

        def person(indi_id, prefix='', suffix='', bold=False):
            base = ('bold',) if bold else ()
            if prefix:
                w.insert('end', prefix, base)
            tag = f'pers_{indi_id.strip("@")}'
            w.insert('end', describe(self.individuals[indi_id], show_id=self.show_ids.get()),
                     base + ('person_link', tag))
            w.tag_configure(tag, foreground=self._link_color)
            w.tag_bind(tag, '<Button-1>',
                       lambda _, iid=indi_id: self._navigate_to(iid))
            if suffix:
                w.insert('end', suffix, base)
            w.insert('end', '\n')

        start = self.individuals[start_id]
        person(start_id, prefix=RESULT_STARTING_FROM)
        if start['dna_markers']:
            nl(RESULT_DNA_FLAGGED_NOTE)
            for m in start['dna_markers']:
                nl(f"    - {self._format_marker(m)}")
        nl()

        if not results:
            nl(RESULT_NO_DNA_FOUND)
        else:
            ancestors = get_ancestor_depths(
                start_id, self.individuals, self.families)
            descendants = get_descendant_depths(
                start_id, self.individuals, self.families)
            hr()
            for rank, (dist, path) in enumerate(results, 1):
                end_id = path[-1][0]
                person(end_id,
                       prefix=RESULT_RANK_PREFIX.format(rank=rank),
                       suffix=RESULT_DISTANCE.format(dist=dist), bold=True)
                rel = describe_relationship(
                    path, self.individuals,
                    ancestors=ancestors, descendants=descendants)
                nl(RESULT_RELATIONSHIP.format(rel=rel))
                nl(RESULT_PATH)
                for i, (node_id, edge) in enumerate(path):
                    if i == 0:
                        person(node_id, prefix="     ")
                    else:
                        person(node_id, prefix=RESULT_EDGE.format(edge=edge))
                nl(RESULT_DNA_MARKERS)
                for m in self.individuals[end_id]['dna_markers']:
                    nl(f"     - {self._format_marker(m)}")
                hr()

        # Family section
        nl(FAM_SECTION, bold=True)
        family_found = False

        parents, siblings, children = self._get_family_members(start_id)

        if parents:
            family_found = True
            nl(FAM_PARENTS)
            for pid in parents:
                person(pid, prefix="    ")

        if siblings:
            family_found = True
            nl(FAM_SIBLINGS)
            for sib_id in siblings:
                person(sib_id, prefix="    ")

        if children:
            family_found = True
            nl(FAM_CHILDREN)
            for child_id in children:
                person(child_id, prefix="    ")

        if not family_found:
            nl(FAM_NO_INFO)
        nl()

        # Home person relationship
        home_id = self._home_person_id
        if home_id and home_id != start_id and home_id in self.individuals:
            hr()
            nl(RESULT_PATH_SECTION, bold=True)
            person(home_id, prefix=RESULT_HOME)
            try:
                max_depth = int(self.max_depth.get())
            except (tk.TclError, ValueError):
                max_depth = 50
            home_paths, _ = bfs_find_all_paths(
                start_id, home_id, self.individuals, self.families,
                top_n=1, max_depth=max_depth,
            )
            if not home_paths:
                nl(RESULT_NO_HOME_PATH)
            else:
                path = home_paths[0]
                ancestors = get_ancestor_depths(
                    start_id, self.individuals, self.families)
                descendants = get_descendant_depths(
                    start_id, self.individuals, self.families)
                rel = describe_relationship(
                    path, self.individuals,
                    ancestors=ancestors, descendants=descendants)
                dist = len(path) - 1
                nl(RESULT_HOME_REL.format(
                    rel=rel, dist=dist, plural='s' if dist != 1 else ''))
                nl(RESULT_HOME_PATH)
                for i, (node_id, edge) in enumerate(path):
                    if i == 0:
                        person(node_id, prefix="  ")
                    else:
                        person(node_id, prefix=RESULT_HOME_EDGE.format(edge=edge))
            nl()

        w.configure(state='disabled')

    def _copy_results(self):
        """Copy the current results text to the clipboard."""
        text = self.results.get('1.0', 'end').rstrip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _clear_results(self):
        """Clear result output and reset search focus."""
        self.results.configure(state='normal')
        self.results.delete('1.0', 'end')
        self.results.configure(state='disabled')
        self.search_text.set('')
        self._last_result = None
        self._kb_focus_search()

    def _format_marker(self, marker):
        """Strip the trailing (@ref@) from a DNA marker string when Show IDs is off."""
        if self.show_ids.get():
            return marker
        return re.sub(r'\s*\(@[^@]+@\)\s*$', '', marker)

    def _clear_person_tags(self, widget):
        """Remove generated person-link tags from a Text widget."""
        for tag in widget.tag_names():
            if tag.startswith('pers_'):
                widget.tag_delete(tag)

    def _navigate_to(self, indi_id):
        """Select a person in the list and render their DNA match results."""
        if not self.tree.exists(indi_id):
            self.search_text.set('')
            self.filter_text.set('')
            self.show_flagged_only.set(False)
            self._populate_tree()
        if self.tree.exists(indi_id):
            self.tree.selection_set(indi_id)
            self.tree.see(indi_id)
            self.tree.focus(indi_id)
        try:
            top_n = int(self.top_n.get())
            max_depth = int(self.max_depth.get())
        except (tk.TclError, ValueError):
            return
        results = bfs_find_dna_matches(
            indi_id, self.individuals, self.families,
            top_n=top_n, max_depth=max_depth,
        )
        self._last_result = {'type': 'dna_matches', 'start_id': indi_id}
        self._render_results(indi_id, results)

    def _display_name(self, indi):
        """Return an individual's display name using the configured name order."""
        if self._name_order == 'last_first':
            surname = indi.get('surname', '')
            given = indi.get('given_name', '')
            if surname and given:
                return f"{surname}, {given}"
            if surname:
                return surname
        return indi['name'] or '(unknown)'

    def _get_family_members(self, indi_id):
        """Return (parents, siblings, children) lists for an individual."""
        indi = self.individuals[indi_id]
        parents, siblings, children = [], [], []
        for fam_id in indi['famc']:
            fam = self.families.get(fam_id)
            if not fam:
                continue
            for pid in (fam['husb'], fam['wife']):
                if pid and pid in self.individuals:
                    parents.append(pid)
            for sib_id in fam['chil']:
                if sib_id != indi_id and sib_id in self.individuals:
                    siblings.append(sib_id)
        for fam_id in indi['fams']:
            fam = self.families.get(fam_id)
            if not fam:
                continue
            for child_id in fam['chil']:
                if child_id in self.individuals:
                    children.append(child_id)
        return parents, siblings, children


def main():
    """Parse command-line options, create the GUI, and start the event loop."""
    parser = argparse.ArgumentParser(
        description='GEDCOM DNA Finder GUI. '
                    'Optionally pass a GEDCOM file path to load it on startup.'
    )
    parser.add_argument(
        'gedcom', nargs='?', default=None,
        help='Optional path to a .ged file to load automatically on startup.'
    )
    args = parser.parse_args()

    root = tk.Tk()
    app = DNAMatchFinderApp(root)

    if args.gedcom:
        path = os.path.abspath(os.path.expanduser(args.gedcom))
        app.gedcom_path.set(path)
        if os.path.isfile(path):
            # Defer the load until after the window is mapped, so the
            # status bar and cursor change are visible during parsing.
            root.after(50, app._load_file)
        else:
            root.after(
                50,
                lambda p=path: messagebox.showerror(
                    ERR_FILE_NOT_FOUND_TITLE,
                    ERR_GEDCOM_NOT_FOUND_MSG.format(path=p),
                ),
            )

    root.mainloop()


if __name__ == '__main__':
    main()
