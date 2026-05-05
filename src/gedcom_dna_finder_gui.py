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
from tkinter import ttk, filedialog, messagebox, scrolledtext
import tkinter as tk
from gedcom_data_model import GedcomDataModel
from gedcom_config import ConfigManager
from gedcom_strings import *  # noqa: F401,F403  (all user-facing strings)
from gedcom_core import (
    bfs_find_dna_matches,
    bfs_find_all_paths,
    describe,
    extract_ged_from_zip,
)
import argparse
import difflib
import os
import re
import sys
import webbrowser
from collections import deque


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


# Inline markdown: image (skip), link, bold, italic, code
_INLINE_RE = re.compile(
    r'!\[[^\]]*\]\([^)]*\)'      # image – discard, no capture groups
    r'|\[([^\]]+)\]\(([^)]+)\)'  # link: g1 = display text, g2 = URL
    r'|\*\*(.+?)\*\*'            # bold: g3
    r'|\*(.+?)\*'                # italic: g4
    r'|`(.+?)`'                  # inline code: g5
)


def _visual_len(text):
    """Return rendered length of markdown text after stripping markup markers."""
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    t = re.sub(r'\*(.+?)\*', r'\1', t)
    t = re.sub(r'`(.+?)`', r'\1', t)
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', t)
    return len(t)


# ---------------------------------------------------------------------------
# GEDCOM event helpers
# ---------------------------------------------------------------------------

def _extract_event(raw, event_tag):
    """Return (date_str, place_str) for the first occurrence of event_tag in raw."""
    date, place = '', ''
    in_event = False
    for level, _xref, tag, value in raw:
        if level == 1:
            if tag == event_tag:
                in_event = True
                date, place = '', ''
            elif in_event:
                break  # left the event's sub-records
            else:
                in_event = False
        elif in_event and level == 2:
            if tag == 'DATE' and not date:
                date = value.strip()
            elif tag == 'PLAC' and not place:
                place = value.strip()
    return date, place


# ---------------------------------------------------------------------------
# Relationship narrative helpers
# ---------------------------------------------------------------------------

def _edge_to_term(edge, sex):
    """Single edge + target sex → plain-English word."""
    if edge in ('father', 'mother'):
        return edge
    if edge == 'sibling':
        return 'brother' if sex == 'M' else ('sister' if sex == 'F' else 'sibling')
    if edge == 'child':
        return 'son' if sex == 'M' else ('daughter' if sex == 'F' else 'child')
    if edge == 'spouse':
        return 'husband' if sex == 'M' else ('wife' if sex == 'F' else 'spouse')
    return edge


_ORDINALS = ['', 'first', 'second', 'third', 'fourth', 'fifth',
             'sixth', 'seventh', 'eighth', 'ninth', 'tenth']
_REMOVALS = {1: 'once', 2: 'twice', 3: 'three times',
             4: 'four times', 5: 'five times'}


def _nth_great(n):
    """Return 'great-' for n==1, '2nd-great-' for n==2, '3rd-great-' for n==3, etc.

    n==0 returns ''. Used to build compact ancestor/descendant labels.
    """
    if n == 0:
        return ''
    if n == 1:
        return 'great-'
    if 11 <= n % 100 <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}-great-'


def get_ancestor_depths(start_id, individuals, families):
    """BFS over father/mother edges only → {ancestor_id: depth from start}."""
    depths = {}
    queue = deque([(start_id, 0)])
    visited = {start_id}
    while queue:
        current_id, depth = queue.popleft()
        indi = individuals.get(current_id)
        if not indi:
            continue
        for fam_id in indi['famc']:
            fam = families.get(fam_id)
            if not fam:
                continue
            for parent_id in (fam['husb'], fam['wife']):
                if parent_id and parent_id not in visited:
                    visited.add(parent_id)
                    depths[parent_id] = depth + 1
                    queue.append((parent_id, depth + 1))
    return depths


def get_descendant_depths(start_id, individuals, families):
    """BFS over child edges only → {descendant_id: depth from start}."""
    depths = {}
    queue = deque([(start_id, 0)])
    visited = {start_id}
    while queue:
        current_id, depth = queue.popleft()
        indi = individuals.get(current_id)
        if not indi:
            continue
        for fam_id in indi['fams']:
            fam = families.get(fam_id)
            if not fam:
                continue
            for child_id in fam['chil']:
                if child_id and child_id not in visited:
                    visited.add(child_id)
                    depths[child_id] = depth + 1
                    queue.append((child_id, depth + 1))
    return depths


def describe_relationship(path, individuals, ancestors=None, descendants=None):
    """Return a plain-English relationship from path[0] to path[-1].

    Recognizes ancestors, descendants, siblings, spouses, cousins (all degrees
    and removals), aunts/uncles, nieces/nephews, in-laws, and step-relations.
    Falls back to a possessive chain (e.g. "father's brother's son") for paths
    that don't fit a standard pattern.

    ancestors  — optional dict {indi_id: depth} of biological ancestors of
                 path[0], computed by get_ancestor_depths().  When provided,
                 a target who is a known ancestor is always labelled by the
                 direct ancestor term, even if the current path reaches them
                 via a spouse edge (which would otherwise produce "step-X").
    descendants — same idea for biological descendants.
    """
    if len(path) <= 1:
        return 'same person'

    edges = [e for _, e in path[1:]]
    sexes = [individuals.get(nid, {}).get('sex', '') for nid, _ in path]
    target_sex = sexes[-1]
    up_set = {'father', 'mother'}

    def chain():
        return "'s ".join(_edge_to_term(edges[i], sexes[i + 1]) for i in range(len(edges)))

    def segmented():
        """Split at first *internal* spouse crossing and describe each part.

        e.g. [father, sibling, child, child, spouse, father, father, father]
        → "first cousin once removed's wife's great-grandfather"
        Returns None when no useful split is possible.
        """
        sp_idx = next((i for i, e in enumerate(edges) if e == 'spouse'), None)
        if sp_idx is None or sp_idx == 0:
            return None
        seg1 = path[:sp_idx + 1]
        spouse_id = path[sp_idx + 1][0]
        spouse_sex = individuals.get(spouse_id, {}).get('sex', '')
        sp_term = ('wife' if spouse_sex == 'F'
                   else 'husband' if spouse_sex == 'M' else 'spouse')
        seg2 = [(spouse_id, None)] + list(path[sp_idx + 2:])
        rel1 = describe_relationship(seg1, individuals)
        rel2 = describe_relationship(
            seg2, individuals) if len(seg2) > 1 else ''
        return f"{rel1}'s {sp_term}'s {rel2}" if rel2 else f"{rel1}'s {sp_term}"

    def ancestor_term(n, sex):
        if n == 1:
            return 'father' if sex == 'M' else ('mother' if sex == 'F' else 'parent')
        gp = 'grandfather' if sex == 'M' else (
            'grandmother' if sex == 'F' else 'grandparent')
        return _nth_great(n - 2) + gp

    def descendant_term(n, sex):
        if n == 1:
            return 'son' if sex == 'M' else ('daughter' if sex == 'F' else 'child')
        gc = 'grandson' if sex == 'M' else (
            'granddaughter' if sex == 'F' else 'grandchild')
        return _nth_great(n - 2) + gc

    # If the target is a known biological ancestor/descendant, use the direct
    # label regardless of the (possibly indirect) path being examined.  This
    # prevents an alternate route through a spouse edge from producing a
    # spurious "step-" prefix (e.g. me→mother→grandmother→grandfather should
    # still read "grandfather", not "step-grandfather").
    target_id = path[-1][0]
    if ancestors and target_id in ancestors:
        return ancestor_term(ancestors[target_id], target_sex)
    if descendants and target_id in descendants:
        return descendant_term(descendants[target_id], target_sex)

    # Pure ancestor (all father/mother edges)
    if all(e in up_set for e in edges):
        return ancestor_term(len(edges), target_sex)

    # Pure descendant (all child edges)
    if all(e == 'child' for e in edges):
        return descendant_term(len(edges), target_sex)

    # Single edge (sibling, spouse, etc.)
    if len(edges) == 1:
        return _edge_to_term(edges[0], target_sex)

    # Strip exactly one leading or one trailing spouse; anything else → chain
    inner = list(edges)
    lead_sp = trail_sp = 0
    while inner and inner[0] == 'spouse':
        lead_sp += 1
        inner.pop(0)
    while inner and inner[-1] == 'spouse':
        trail_sp += 1
        inner.pop()
    if not inner or lead_sp > 1 or trail_sp > 1 or (lead_sp and trail_sp):
        return segmented() or chain()

    # Validate inner: all-up → optional single sibling → all-down.
    # If the sequence fails because of interior spouse edges (path navigated
    # through a family unit rather than directly), strip those spouse edges
    # and retry — they don't change the fundamental relationship.
    def _classify(seq):
        st = 'up'
        uu = dd = ss = 0
        ok = True
        for e in seq:
            if st == 'up':
                if e in up_set:
                    uu += 1
                elif e == 'sibling':
                    ss += 1
                    st = 'down'
                elif e == 'child':
                    dd += 1
                    st = 'down'
                else:
                    ok = False
                    break
            elif st == 'down':
                if e == 'child':
                    dd += 1
                else:
                    ok = False
                    break
        return uu, dd, ss, ok

    u, d, s, valid = _classify(inner)
    if not valid:
        # Retry 1: strip interior spouse edges (family-unit crossings that
        # don't change the relationship degree).
        no_sp = [e for e in inner if e != 'spouse']
        if no_sp != inner:
            u, d, s, valid = _classify(no_sp)
        else:
            no_sp = inner
        # Retry 2: strip a trailing sibling edge.  The sibling of an Nth
        # cousin / niece / uncle at a given degree is still at that same
        # degree, so the relationship label is unchanged.  (Retry 1 must run
        # first so no_sp is already spouse-free before we trim the tail.)
        if not valid and no_sp and no_sp[-1] == 'sibling':
            trimmed = no_sp[:-1]
            if trimmed:
                u, d, s, valid = _classify(trimmed)
    if not valid:
        return segmented() or chain()

    u_eff = u + s
    d_eff = d + s

    # Inner is all-up: spouse + ancestors → in-law; ancestors + spouse → step-
    if d_eff == 0:
        return (ancestor_term(u, target_sex) + '-in-law' if lead_sp
                else 'step-' + ancestor_term(u, target_sex))

    # Inner is all-down: descendants + spouse → in-law; spouse + descendants → step-
    if u_eff == 0:
        return (descendant_term(d, target_sex) + '-in-law' if trail_sp
                else 'step-' + descendant_term(d, target_sex))

    # Cousin-type: compute degree and number of removals
    cn = min(u_eff, d_eff) - 1
    rem = abs(u_eff - d_eff)
    more_desc = d_eff > u_eff   # target is further from the common ancestor

    if cn == 0 and rem == 0:
        core = 'brother' if target_sex == 'M' else (
            'sister' if target_sex == 'F' else 'sibling')
    elif cn == 0:
        if more_desc:
            core = 'nephew' if target_sex == 'M' else (
                'niece' if target_sex == 'F' else 'niece/nephew')
        else:
            core = 'uncle' if target_sex == 'M' else (
                'aunt' if target_sex == 'F' else 'uncle/aunt')
        if rem > 1:
            core = _nth_great(rem - 1) + core
    else:
        n_str = _ORDINALS[cn] if cn < len(_ORDINALS) else f'{cn}th'
        r_str = _REMOVALS.get(rem, f'{rem} times')
        core = f'{n_str} cousin' + (f' {r_str} removed' if rem else '')

    return core + '-in-law' if (lead_sp or trail_sp) else core


# ===========================================================================
# GUI
# ===========================================================================

class DNAMatchFinderApp:
    """Tkinter application for browsing GEDCOM people and finding DNA matches."""

    MAX_LIST_DISPLAY = 2000  # cap visible rows in the people list
    FUZZY_THRESHOLD = 0.72   # minimum SequenceMatcher ratio to count as a match
    MAX_RECENT = 10          # number of recent files to remember
    _FONT_SIZES = {
        'small':  {'ui': 9,  'mono': 9},
        'medium': {'ui': 10, 'mono': 10},
        'large':  {'ui': 13, 'mono': 12},
    }
    _THEME_NAMES = ('Default', 'Light', 'Dark', 'Blue', 'Green')
    _THEMES = {
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
        ttk.Button(file_frame, text=BTN_BROWSE,
                   command=self._browse).pack(side='left', padx=2)
        ttk.Button(file_frame, text=BTN_LOAD, command=self._load_file).pack(
            side='left', padx=2)

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
        status = ttk.Label(outer, textvariable=self.status_text,
                           relief='sunken', anchor='w')
        status.pack(fill='x', pady=(8, 0))

        self._setup_keybindings()

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

        self.status_text.set(STATUS_LOADING)
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            from_cache, encoding_warning = self._model.load(
                gedcom_path,
                dna_keyword=self.tag_keyword.get(),
                page_marker=self.page_marker.get(),
                cache_dir=self._cache_dir(),
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.root.config(cursor="")
            self.status_text.set(STATUS_LOAD_FAILED)
            messagebox.showerror(
                ERR_PARSE_TITLE, ERR_PARSE_MSG.format(error=e))
            return
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        self.individuals = self._model.individuals
        self.families = self._model.families
        self.tag_records = self._model.tag_records

        if encoding_warning:
            messagebox.showwarning(ERR_ENCODING_TITLE, encoding_warning)

        self.sorted_ids = sorted(
            self.individuals.keys(),
            key=lambda iid: (self.individuals[iid]['name'].lower(), iid),
        )
        self.root.config(cursor="")
        self._add_to_history(path)
        self._home_person_id = self._load_home_person(path)
        self._populate_tree()
        status = (STATUS_LOADED_CACHED.format(count=len(self.individuals))
                  if from_cache
                  else STATUS_LOADED.format(count=len(self.individuals)))
        self.status_text.set(status)

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
        results = self._model.find_dna_matches(start_id, top_n, max_depth)
        self._last_result = {'type': 'dna_matches', 'start_id': start_id}
        self._render_results(start_id, results)

    def _show_person(self):
        """Open the GEDCOM record viewer for the selected person."""
        if not self.individuals:
            messagebox.showwarning(ERR_NO_DATA_TITLE, ERR_NO_DATA_MSG)
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(ERR_NO_SEL_TITLE, ERR_NO_SEL_MSG)
            return
        self._show_person_for(sel[0])

    def _show_person_for(self, indi_id):
        """Open a detail window for a specific individual ID."""
        win = tk.Toplevel(self.root)
        win.geometry(self._show_person_geometry or "700x520")
        win.minsize(400, 300)
        win.focus_force()

        _geo_after = [None]

        def _on_win_configure(event):
            if event.widget is not win:
                return
            if _geo_after[0]:
                win.after_cancel(_geo_after[0])
            _geo_after[0] = win.after(
                400, lambda: self._persist_show_person_geometry(win))

        win.bind('<Configure>', _on_win_configure)
        win.bind('<Escape>', lambda _: win.destroy())
        win.bind('<Up>', lambda _: text.yview_scroll(-1, 'units') or 'break')
        win.bind('<Down>', lambda _: text.yview_scroll(1, 'units') or 'break')
        win.bind('<Prior>', lambda _: text.yview_scroll(-1, 'pages') or 'break')
        win.bind('<Next>', lambda _: text.yview_scroll(1, 'pages') or 'break')
        win.bind('<Home>', lambda _: text.yview_moveto(0) or 'break')
        win.bind('<End>', lambda _: text.yview_moveto(1) or 'break')

        text = scrolledtext.ScrolledText(
            win, font=self._mono_font, wrap='none', padx=8, pady=8)
        text.pack(fill='both', expand=True)
        text.tag_configure('bold', font=self._mono_font_bold)
        text.tag_configure('person_link')
        text.tag_bind('person_link', '<Enter>',
                      lambda _: text.config(cursor='hand2'))
        text.tag_bind('person_link', '<Leave>',
                      lambda _: text.config(cursor=''))

        def populate(iid):
            indi = self.individuals[iid]
            win.title(WIN_GEDCOM_RECORD.format(name=indi['name'] or iid))
            text.configure(state='normal')
            text.delete('1.0', 'end')
            self._clear_person_tags(text)

            def add(line, bold=False):
                text.insert('end', line + '\n', ('bold',) if bold else ())

            def person(pid, prefix=''):
                if prefix:
                    text.insert('end', prefix)
                tag = f'pers_{pid.strip("@")}'
                text.insert('end', describe(self.individuals[pid], show_id=self.show_ids.get()),
                            ('person_link', tag))
                text.tag_configure(
                    tag, foreground=self._link_color, underline=True)
                text.tag_bind(tag, '<Button-1>',
                              lambda _, p=pid: populate(p))
                text.insert('end', '\n')

            add(BIO_SECTION, bold=True)
            bio_found = False

            def fmt_event(date, place):
                parts = [p for p in (date, place) if p]
                return ', '.join(parts)

            b_date, b_place = _extract_event(indi['_raw'], 'BIRT')
            if b_date or b_place:
                bio_found = True
                add(BIO_BORN.format(event=fmt_event(b_date, b_place)))

            for fam_id in indi['fams']:
                fam = self.families.get(fam_id)
                if not fam:
                    continue
                m_date = fam.get('marr_date', '')
                m_place = fam.get('marr_place', '')
                spouse_id = fam['wife'] if fam['husb'] == iid else fam['husb']
                spouse_name = (self._display_name(self.individuals[spouse_id])
                               if spouse_id and spouse_id in self.individuals else '')
                if spouse_name or m_date or m_place:
                    bio_found = True
                    parts = [p for p in (spouse_name, m_date, m_place) if p]
                    add(BIO_MARRIED.format(spouses=', '.join(parts)))

            d_date, d_place = _extract_event(indi['_raw'], 'DEAT')
            if d_date or d_place:
                bio_found = True
                add(BIO_DIED.format(event=fmt_event(d_date, d_place)))

            bu_date, bu_place = _extract_event(indi['_raw'], 'BURI')
            if bu_date or bu_place:
                bio_found = True
                add(BIO_BURIED.format(event=fmt_event(bu_date, bu_place)))

            if not bio_found:
                add(BIO_NO_INFO)
            add("")

            add(FAM_SECTION, bold=True)
            family_found = False

            parents = []
            for fam_id in indi['famc']:
                fam = self.families.get(fam_id)
                if not fam:
                    continue
                for pid in (fam['husb'], fam['wife']):
                    if pid and pid in self.individuals:
                        parents.append(pid)
            if parents:
                family_found = True
                add(FAM_PARENTS)
                for pid in parents:
                    person(pid, prefix="    ")

            siblings = []
            for fam_id in indi['famc']:
                fam = self.families.get(fam_id)
                if not fam:
                    continue
                for sib_id in fam['chil']:
                    if sib_id != iid and sib_id in self.individuals:
                        siblings.append(sib_id)
            if siblings:
                family_found = True
                add(FAM_SIBLINGS)
                for sib_id in siblings:
                    person(sib_id, prefix="    ")

            children = []
            for fam_id in indi['fams']:
                fam = self.families.get(fam_id)
                if not fam:
                    continue
                for child_id in fam['chil']:
                    if child_id in self.individuals:
                        children.append(child_id)
            if children:
                family_found = True
                add(FAM_CHILDREN)
                for child_id in children:
                    person(child_id, prefix="    ")

            if not family_found:
                add(FAM_NO_INFO)
            add("")
            add(GEDCOM_SECTION, bold=True)

            for level, xref, tag, value in indi.get('_raw', []):
                parts = [str(level)]
                if xref and self.show_ids.get():
                    parts.append(xref)
                parts.append(tag)
                if value:
                    parts.append(value)
                add(' '.join(parts))

            text.configure(state='disabled')

        populate(indi_id)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill='x', pady=(4, 8))
        ttk.Button(btn_frame, text=BTN_CLOSE, command=win.destroy).pack(
            side='right', padx=8)

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

        def person(indi_id, prefix='', suffix='', bold=False):
            base = ('bold',) if bold else ()
            if prefix:
                w.insert('end', prefix, base)
            tag = f'pers_{indi_id.strip("@")}'
            w.insert('end', describe(self.individuals[indi_id], show_id=self.show_ids.get()),
                     base + ('person_link', tag))
            w.tag_configure(tag, foreground=self._link_color, underline=True)
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
            for rank, (dist, path) in enumerate(results, 1):
                end_id = path[-1][0]
                person(end_id,
                       prefix=RESULT_RANK_PREFIX.format(rank=rank),
                       suffix=RESULT_DISTANCE.format(dist=dist), bold=True)
                nl(RESULT_DNA_MARKERS)
                for m in self.individuals[end_id]['dna_markers']:
                    nl(f"     - {self._format_marker(m)}")
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
                nl()

        # Family section
        nl(FAM_SECTION, bold=True)
        family_found = False

        parents = []
        for fam_id in start['famc']:
            fam = self.families.get(fam_id)
            if not fam:
                continue
            for pid in (fam['husb'], fam['wife']):
                if pid and pid in self.individuals:
                    parents.append(pid)
        if parents:
            family_found = True
            nl(FAM_PARENTS)
            for pid in parents:
                person(pid, prefix="    ")

        siblings = []
        for fam_id in start['famc']:
            fam = self.families.get(fam_id)
            if not fam:
                continue
            for sib_id in fam['chil']:
                if sib_id != start_id and sib_id in self.individuals:
                    siblings.append(sib_id)
        if siblings:
            family_found = True
            nl(FAM_SIBLINGS)
            for sib_id in siblings:
                person(sib_id, prefix="    ")

        children = []
        for fam_id in start['fams']:
            fam = self.families.get(fam_id)
            if not fam:
                continue
            for child_id in fam['chil']:
                if child_id in self.individuals:
                    children.append(child_id)
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

    def _view_tags(self):
        """Show tag-record definitions and allow choosing the DNA tag keyword."""
        if not self.tag_records:
            messagebox.showinfo(WIN_TAG_DEFINITIONS, MSG_NO_TAGS)
            return

        win = tk.Toplevel(self.root)
        win.title(WIN_TAG_DEFINITIONS)
        win.transient(self.root)
        win.resizable(True, True)

        show_ids = self.show_ids.get()
        rows = sorted(self.tag_records.items())  # [(ref, name), ...]

        list_frame = ttk.Frame(win, padding=(8, 8, 8, 0))
        list_frame.pack(fill='both', expand=True)

        if show_ids:
            tag_tree = ttk.Treeview(list_frame, columns=('id', 'name'),
                                    show='headings', selectmode='browse',
                                    height=min(len(rows), 20))
            tag_tree.heading('id', text=COL_TAG_ID)
            tag_tree.heading('name', text=COL_TAG_NAME)
            tag_tree.column('id', width=90, anchor='w', stretch=False)
            tag_tree.column('name', width=300, anchor='w', stretch=True)
        else:
            tag_tree = ttk.Treeview(list_frame, columns=('name',),
                                    show='headings', selectmode='browse',
                                    height=min(len(rows), 20))
            tag_tree.heading('name', text=COL_TAG_NAME)
            tag_tree.column('name', width=390, anchor='w', stretch=True)

        ysb = ttk.Scrollbar(list_frame, orient='vertical',
                            command=tag_tree.yview)
        ysb.configure(takefocus=False)
        tag_tree.configure(yscrollcommand=ysb.set)
        tag_tree.pack(side='left', fill='both', expand=True)
        ysb.pack(side='right', fill='y')

        current_kw = self.tag_keyword.get().strip().lower()
        first_match = None
        for ref, name in rows:
            iid = tag_tree.insert('', 'end',
                                  values=(ref, name) if show_ids else (name,))
            if first_match is None and name.strip().lower() == current_kw:
                first_match = iid

        btn_frame = ttk.Frame(win, padding=(8, 4, 8, 8))
        btn_frame.pack(fill='x')

        def on_ok():
            sel = tag_tree.selection()
            if sel:
                name_val = tag_tree.set(sel[0], 'name')
                self.tag_keyword.set(name_val)
            win.destroy()

        def on_cancel():
            win.destroy()

        ttk.Button(btn_frame, text=BTN_OK,
                   command=on_ok).pack(side='right', padx=(4, 0))
        ttk.Button(btn_frame, text=BTN_CANCEL,
                   command=on_cancel).pack(side='right')

        tag_tree.bind('<Return>', lambda _: on_ok())
        tag_tree.bind('<Home>', lambda _: self._tree_jump(
            'first', tag_tree) or 'break')
        tag_tree.bind('<End>', lambda _: self._tree_jump(
            'last',  tag_tree) or 'break')
        win.bind('<Escape>', lambda _: on_cancel())

        # Size window to fit content, then centre over main window
        win.update_idletasks()
        self.root.update_idletasks()
        req_w = win.winfo_reqwidth()
        req_h = win.winfo_reqheight()
        px = self.root.winfo_x() + (self.root.winfo_width() - req_w) // 2
        py = self.root.winfo_y() + (self.root.winfo_height() - req_h) // 2
        win.geometry(f"{req_w}x{req_h}+{px}+{py}")

        win.focus_force()
        tag_tree.focus_set()
        target = first_match or (tag_tree.get_children()[
                                 0] if tag_tree.get_children() else None)
        if target:
            tag_tree.focus(target)
            tag_tree.selection_set(target)
            tag_tree.see(target)

    def _pick_person(self, title=WIN_SELECT_PERSON):
        """Modal dialog to pick one person from the loaded GEDCOM. Returns indi_id or None."""
        if not self.individuals:
            messagebox.showwarning(ERR_NO_DATA_TITLE, ERR_NO_DATA_MSG)
            return None

        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.focus_force()

        dw, dh = 600, 500
        self.root.update_idletasks()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        x = px + (pw - dw) // 2
        y = py + (ph - dh) // 2
        dialog.geometry(f"{dw}x{dh}+{x}+{y}")

        result = [None]

        search_frame = ttk.Frame(dialog, padding=8)
        search_frame.pack(fill='x')
        ttk.Label(search_frame, text=LBL_FIND).pack(side='left', padx=(0, 4))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var)
        search_entry.pack(side='left', fill='x', expand=True)

        list_frame = ttk.Frame(dialog, padding=(8, 0, 8, 0))
        list_frame.pack(fill='both', expand=True)

        picker_tree = ttk.Treeview(
            list_frame,
            columns=('name', 'birth', 'death', 'flagged'),
            show='headings',
            selectmode='browse',
        )
        picker_tree.heading('name', text=COL_NAME)
        picker_tree.heading('birth', text=COL_BIRTH)
        picker_tree.heading('death', text=COL_DEATH)
        picker_tree.heading('flagged', text=COL_DNA)
        picker_tree.column('name', width=240, anchor='w', stretch=True)
        picker_tree.column('birth', width=55, anchor='w', stretch=False)
        picker_tree.column('death', width=55, anchor='w', stretch=False)
        picker_tree.column('flagged', width=50, anchor='center', stretch=False)
        picker_tree.tag_configure('flagged_row', background='#fff4cc')

        ysb = ttk.Scrollbar(list_frame, orient='vertical',
                            command=picker_tree.yview)
        picker_tree.configure(yscrollcommand=ysb.set)
        picker_tree.pack(side='left', fill='both', expand=True)
        ysb.pack(side='right', fill='y')

        after_id = [None]

        def populate(query=''):
            picker_tree.delete(*picker_tree.get_children())
            query_l = query.strip().lower()
            query_tokens = query_l.split()
            shown = 0
            for indi_id in self.sorted_ids:
                indi = self.individuals[indi_id]
                if query_tokens:
                    all_names = indi['alt_names'] or [indi['name']]
                    if not (
                        any(all(tok in name.lower() for tok in query_tokens)
                            for name in all_names)
                        or query_l in indi_id.lower()
                    ):
                        continue
                tags = ('flagged_row',) if indi['dna_markers'] else ()
                flagged_mark = '✓' if indi['dna_markers'] else ''
                picker_tree.insert(
                    '', 'end', iid=indi_id,
                    values=(self._display_name(indi),
                            indi['birth_year'] or '',
                            indi['death_year'] or '',
                            flagged_mark),
                    tags=tags,
                )
                shown += 1
                if shown >= self.MAX_LIST_DISPLAY:
                    break

        def on_search_change(*_):
            if after_id[0]:
                dialog.after_cancel(after_id[0])
            after_id[0] = dialog.after(150, lambda: populate(search_var.get()))

        def picker_flush_and_jump():
            if after_id[0]:
                dialog.after_cancel(after_id[0])
                after_id[0] = None
                populate(search_var.get())
            self._tree_jump('first', picker_tree)

        search_var.trace_add('write', on_search_change)
        search_entry.bind('<Return>', lambda _: picker_flush_and_jump())
        populate()
        search_entry.focus_set()

        def select():
            sel = picker_tree.selection()
            if sel:
                result[0] = sel[0]
            dialog.destroy()

        picker_tree.bind('<Double-1>', lambda e: select())
        picker_tree.bind('<Return>', lambda e: select())
        picker_tree.bind(
            '<Key>', lambda e: self._tree_type_ahead(e, picker_tree))
        picker_tree.bind('<Home>', lambda _: self._tree_jump(
            'first', picker_tree) or 'break')
        picker_tree.bind('<End>', lambda _: self._tree_jump(
            'last',  picker_tree) or 'break')
        dialog.bind('<Escape>', lambda _: dialog.destroy())

        btn_frame = ttk.Frame(dialog, padding=8)
        btn_frame.pack(fill='x')
        ttk.Button(btn_frame, text=BTN_SELECT, command=select).pack(
            side='right', padx=(4, 0))
        ttk.Button(btn_frame, text=BTN_CANCEL,
                   command=dialog.destroy).pack(side='right')

        dialog.wait_window()
        return result[0]

    def _find_path(self):
        """Prompt for a target person and render paths from the current selection."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(ERR_NO_SEL_TITLE, ERR_NO_PATH_SEL_MSG)
            return
        start_id = sel[0]

        target_id = self._pick_person(WIN_SELECT_TARGET)
        if not target_id:
            return

        try:
            max_depth = int(self.max_depth.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(ERR_BAD_VAL_TITLE, ERR_BAD_VAL_DEPTH)
            return

        try:
            top_n = int(self.top_n.get())
        except (tk.TclError, ValueError):
            top_n = 5
        paths, truncated = self._model.find_all_paths(
            start_id, target_id, top_n, max_depth)
        self._last_result = {'type': 'path',
                             'start_id': start_id, 'end_id': target_id}
        self._render_path_results(start_id, target_id, paths, truncated)

    def _render_path_results(self, start_id, end_id, paths, truncated=False):
        """Render relationship paths between two selected individuals."""
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

        def person(indi_id, prefix='', suffix=''):
            if prefix:
                w.insert('end', prefix)
            tag = f'pers_{indi_id.strip("@")}'
            w.insert('end', describe(self.individuals[indi_id], show_id=self.show_ids.get()),
                     ('person_link', tag))
            w.tag_configure(tag, foreground=self._link_color, underline=True)
            w.tag_bind(tag, '<Button-1>',
                       lambda _, iid=indi_id: self._navigate_to(iid))
            if suffix:
                w.insert('end', suffix)
            w.insert('end', '\n')

        nl(PATH_SECTION, bold=True)
        person(start_id, prefix=PATH_FROM)
        person(end_id,   prefix=PATH_TO)
        nl()

        if start_id == end_id:
            nl(PATH_SAME_PERSON)
        elif not paths:
            nl(PATH_NOT_FOUND.format(depth=self.max_depth.get()))
        else:
            ancestors = get_ancestor_depths(
                start_id, self.individuals, self.families)
            descendants = get_descendant_depths(
                start_id, self.individuals, self.families)
            for rank, path in enumerate(paths, 1):
                dist = len(path) - 1
                rel = describe_relationship(path, self.individuals,
                                            ancestors=ancestors, descendants=descendants)
                nl(PATH_RANK.format(
                    rank=rank, rel=rel, dist=dist,
                    plural='s' if dist != 1 else ''), bold=True)
                for i, (node_id, edge) in enumerate(path):
                    if i == 0:
                        person(node_id, prefix="  ")
                    else:
                        person(node_id, prefix=PATH_EDGE.format(edge=edge))
                nl()
            if truncated:
                nl(PATH_SEARCH_CAP)
        nl()

        w.configure(state='disabled')

    # ---------------------------------------------------------- History / config
    def _cache_dir(self):
        """Return the directory used for GEDCOM parse caches."""
        return self._config._path.parent / 'cache'

    def _load_history(self):
        """Load the recently opened GEDCOM file list."""
        return self._config.get_recent_files()

    def _save_history(self, history):
        """Persist the recently opened GEDCOM file list."""
        self._config.set_recent_files(history)

    def _add_to_history(self, filepath):
        """Add filepath to the recent-file list and update the combo box."""
        history = [filepath] + [p for p in self._recent_files if p != filepath]
        history = history[:self.MAX_RECENT]
        self._recent_files = history
        self.path_combo['values'] = history
        self._config.set_recent_files(history)

    def _clear_cache(self):
        """Confirm and remove cached GEDCOM parse files."""
        cache_dir = self._cache_dir()
        files = list(cache_dir.glob('*.json')) if cache_dir.exists() else []
        if not files:
            messagebox.showinfo(CACHE_EMPTY_TITLE, CACHE_EMPTY_MSG)
            return
        if messagebox.askyesno(
            CACHE_CLEAR_TITLE,
            CACHE_CLEAR_MSG.format(count=len(files)),
        ):
            deleted = self._model.clear_cache(cache_dir)
            messagebox.showinfo(
                CACHE_DONE_TITLE, CACHE_DONE_MSG.format(deleted=deleted))

    def _load_home_person(self, gedcom_path):
        """Load the saved home person ID for gedcom_path."""
        return self._config.get_home_person(gedcom_path)

    def _save_home_person(self, gedcom_path, indi_id):
        """Persist the home person ID for gedcom_path."""
        self._config.set_home_person(gedcom_path, indi_id)

    def _load_font_preference(self):
        """Load the saved UI font-size preference."""
        return self._config.get_font_preference(self._FONT_SIZES)

    def _save_font_preference(self, size_name):
        """Persist the UI font-size preference."""
        self._config.set_font_preference(size_name)

    def _apply_font_size(self, size_name):
        """Apply a named font-size preset to UI and monospace fonts."""
        sizes = self._FONT_SIZES[size_name]
        mono_sz = sizes['mono']
        ui_sz = sizes['ui']

        self._mono_font.configure(size=mono_sz)
        self._mono_font_bold.configure(size=mono_sz)

        for fname in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkSmallCaptionFont'):
            try:
                tkfont.nametofont(fname).configure(size=ui_sz)
            except tk.TclError:
                pass

        self._apply_styles()

        if hasattr(self, 'results'):
            self.root.after(0, self._refit_windows)

    def _refit_windows(self):
        """Grow open windows as needed to fit the current font metrics."""
        self.root.update_idletasks()
        req_w = self.root.winfo_reqwidth()
        cur_w = self.root.winfo_width()
        cur_h = self.root.winfo_height()
        self.root.minsize(max(req_w, 800), 500)
        if cur_w < req_w:
            self.root.geometry(f"{req_w}x{cur_h}")
        for win in self.root.winfo_children():
            if not isinstance(win, tk.Toplevel):
                continue
            try:
                rw = win.winfo_reqwidth()
                rh = win.winfo_reqheight()
                cw = win.winfo_width()
                ch = win.winfo_height()
                new_w = max(cw, rw)
                new_h = max(ch, rh)
                if new_w != cw or new_h != ch:
                    win.geometry(f"{new_w}x{new_h}")
            except tk.TclError:
                pass

    def _apply_styles(self):
        """Apply the current theme colours + font metrics to the ttk Style engine."""
        style = ttk.Style()
        t = self._THEMES.get(getattr(self, '_theme_pref', 'Default'))

        if t is None:
            try:
                style.theme_use(self._default_ttk_theme)
            except tk.TclError:
                pass
        else:
            try:
                style.theme_use(t['ttk'])
            except tk.TclError:
                pass
            bg, fg = t['bg'], t['fg']
            bbg, fbg = t['button_bg'], t['field_bg']
            sel_bg, sel_fg = t['select_bg'], t['select_fg']
            hbg, tr = t['heading_bg'], t['trough']

            style.configure('.', background=bg, foreground=fg)
            style.configure('TFrame', background=bg)
            style.configure('TLabelframe', background=bg, foreground=fg)
            style.configure('TLabelframe.Label', background=bg, foreground=fg)
            style.configure('TLabel', background=bg, foreground=fg)
            style.configure('TButton', background=bbg, foreground=fg)
            style.map('TButton',
                      background=[('active', sel_bg), ('pressed', sel_bg)],
                      foreground=[('active', sel_fg), ('pressed', sel_fg)])
            style.configure('TEntry', fieldbackground=fbg, foreground=fg,
                            selectbackground=sel_bg, selectforeground=sel_fg)
            style.configure('TCombobox', fieldbackground=fbg, foreground=fg,
                            selectbackground=sel_bg, selectforeground=sel_fg,
                            background=bbg, arrowcolor=fg)
            style.map('TCombobox',
                      fieldbackground=[('readonly', fbg)],
                      selectbackground=[('readonly', sel_bg)],
                      foreground=[('readonly', fg)])
            style.configure('TSpinbox', fieldbackground=fbg, foreground=fg,
                            background=bbg, arrowcolor=fg)
            style.configure('TCheckbutton', background=bg, foreground=fg)
            style.map('TCheckbutton', background=[('active', bg)])
            style.configure('TRadiobutton', background=bg, foreground=fg)
            style.map('TRadiobutton', background=[('active', bg)])
            style.configure('TScrollbar', background=bbg, troughcolor=tr,
                            arrowcolor=fg, bordercolor=bg,
                            darkcolor=bbg, lightcolor=bbg)
            style.configure('TPanedwindow', background=bg)
            style.configure('Treeview', background=fbg, foreground=fg,
                            fieldbackground=fbg)
            style.configure('Treeview.Heading', background=hbg, foreground=fg)
            style.map('Treeview',
                      background=[('selected', sel_bg)],
                      foreground=[('selected', sel_fg)])

        row_h = tkfont.nametofont('TkDefaultFont').metrics('linespace') + 6
        style.configure('Treeview', font='TkDefaultFont', rowheight=row_h)
        style.configure('Treeview.Heading', font='TkDefaultFont')

    def _apply_theme(self, theme_name):
        """Apply a named color theme to the application."""
        self._theme_pref = theme_name
        t = self._THEMES.get(theme_name)
        self._apply_styles()
        self._recolor_all(t)
        if hasattr(self, 'tree'):
            self.tree.tag_configure(
                'flagged_row',
                background=t['flag_bg'] if t else '#fff4cc',
            )
        if hasattr(self, 'results'):
            self.root.after(0, self._refit_windows)

    def _recolor_all(self, theme):
        """Recolor every tk.Text widget and window background to match theme."""
        if theme is None:
            text_bg, text_fg = 'white', 'black'
            insert_col = 'black'
            sel_bg, sel_fg = '#0078d4', 'white'
            link_col = '#0066cc'
            root_bg = None
        else:
            text_bg, text_fg = theme['text_bg'], theme['text_fg']
            insert_col = theme['insert']
            sel_bg, sel_fg = theme['select_bg'], theme['select_fg']
            link_col = theme['link']
            root_bg = theme['bg']

        self._link_color = link_col

        def recolor(widget):
            try:
                if isinstance(widget, tk.Text):
                    widget.configure(
                        bg=text_bg, fg=text_fg,
                        insertbackground=insert_col,
                        selectbackground=sel_bg,
                        selectforeground=sel_fg,
                    )
                    if hasattr(widget, 'frame'):
                        widget.frame.configure(bg=text_bg)
                    for tag in widget.tag_names():
                        if tag.startswith('pers_'):
                            widget.tag_configure(tag, foreground=link_col)
                elif isinstance(widget, (tk.Tk, tk.Toplevel)) and root_bg:
                    widget.configure(bg=root_bg)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                recolor(child)

        recolor(self.root)

    def _load_theme_preference(self):
        """Load the saved color theme preference."""
        return self._config.get_theme_preference(self._THEME_NAMES)

    def _save_theme_preference(self, theme_name):
        """Persist the selected color theme preference."""
        self._config.set_theme_preference(theme_name)

    def _show_preferences(self):
        """Open the preferences dialog for display, search, and cache settings."""
        original_font = self._font_size_pref
        original_theme = self._theme_pref
        original_top_n = self.top_n.get()
        original_max_depth = self.max_depth.get()
        original_fuzzy_threshold = self.fuzzy_threshold.get()

        win = tk.Toplevel(self.root)
        win.title(WIN_PREFERENCES)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.withdraw()  # hide until sized; avoids a flicker at the wrong size

        outer = ttk.Frame(win, padding=16)
        outer.pack(fill='both', expand=True)

        font_frame = ttk.LabelFrame(
            outer, text=FRAME_FONT_SIZE, padding=(12, 6))
        font_frame.pack(fill='x', pady=(0, 8))

        size_var = tk.StringVar(value=self._font_size_pref)

        def on_font_change():
            self._apply_font_size(size_var.get())

        for label, key in ((FONT_SMALL, "small"), (FONT_MEDIUM, "medium"), (FONT_LARGE, "large")):
            ttk.Radiobutton(
                font_frame, text=label, variable=size_var, value=key,
                command=on_font_change,
            ).pack(side='left', padx=8)

        theme_frame = ttk.LabelFrame(outer, text=FRAME_THEME, padding=(12, 6))
        theme_frame.pack(fill='x', pady=(0, 8))

        theme_var = tk.StringVar(value=self._theme_pref)

        def on_theme_change():
            self._apply_theme(theme_var.get())

        for name in self._THEME_NAMES:
            ttk.Radiobutton(
                theme_frame, text=name, variable=theme_var, value=name,
                command=on_theme_change,
            ).pack(side='left', padx=6)

        search_frame = ttk.LabelFrame(
            outer, text=FRAME_SEARCH_DEFAULTS, padding=(12, 6))
        search_frame.pack(fill='x', pady=(0, 8))

        _pref_top_n_label = ttk.Label(search_frame, text=LBL_TOP_N_RESULTS)
        _pref_top_n_label.grid(row=0, column=0, sticky='w', padx=(0, 8))
        top_n_var = tk.IntVar(value=self.top_n.get())
        _pref_top_n_spin = ttk.Spinbox(
            search_frame, from_=1, to=20, textvariable=top_n_var, width=6)
        _pref_top_n_spin.grid(row=0, column=1, sticky='w', padx=(0, 24))
        Tooltip(_pref_top_n_label, TIP_TOP_N)
        Tooltip(_pref_top_n_spin, TIP_TOP_N)
        _pref_max_depth_label = ttk.Label(
            search_frame, text=LBL_MAX_DEPTH_PREF)
        _pref_max_depth_label.grid(row=0, column=2, sticky='w', padx=(0, 8))
        max_depth_var = tk.IntVar(value=self.max_depth.get())
        _pref_max_depth_spin = ttk.Spinbox(
            search_frame, from_=1, to=200, textvariable=max_depth_var, width=6)
        _pref_max_depth_spin.grid(row=0, column=3, sticky='w')
        Tooltip(_pref_max_depth_label, TIP_MAX_DEPTH)
        Tooltip(_pref_max_depth_spin, TIP_MAX_DEPTH)
        _pref_fuzzy_threshold_label = ttk.Label(
            search_frame, text=LBL_FUZZY_THRESHOLD)
        _pref_fuzzy_threshold_label.grid(
            row=1, column=0, sticky='w', padx=(0, 8), pady=(6, 0))
        fuzzy_threshold_var = tk.DoubleVar(
            value=round(float(self.fuzzy_threshold.get()), 2))
        _pref_fuzzy_threshold_spin = ttk.Spinbox(
            search_frame, from_=0.0, to=1.0, increment=0.01,
            textvariable=fuzzy_threshold_var, width=6, format="%.2f")
        _pref_fuzzy_threshold_spin.grid(
            row=1, column=1, sticky='w', pady=(6, 0))
        Tooltip(_pref_fuzzy_threshold_label, TIP_FUZZY_THRESHOLD)
        Tooltip(_pref_fuzzy_threshold_spin, TIP_FUZZY_THRESHOLD)

        display_frame = ttk.LabelFrame(
            outer, text=FRAME_DISPLAY, padding=(12, 6))
        display_frame.pack(fill='x', pady=(0, 8))
        show_ids_var = tk.BooleanVar(value=self.show_ids.get())
        ttk.Checkbutton(display_frame, text=CHK_SHOW_IDS,
                        variable=show_ids_var).pack(anchor='w', padx=8)

        name_order_row = ttk.Frame(display_frame)
        name_order_row.pack(anchor='w', padx=8, pady=(4, 0))
        ttk.Label(name_order_row, text=LBL_NAME_FORMAT).pack(
            side='left', padx=(0, 8))
        name_order_var = tk.StringVar(value=self._name_order)
        ttk.Radiobutton(name_order_row, text=NAME_FIRST_LAST,
                        variable=name_order_var, value='first_last').pack(side='left', padx=(0, 8))
        ttk.Radiobutton(name_order_row, text=NAME_LAST_FIRST,
                        variable=name_order_var, value='last_first').pack(side='left')

        cache_frame = ttk.LabelFrame(outer, text=FRAME_CACHE, padding=(12, 6))
        cache_frame.pack(fill='x', pady=(0, 8))
        ttk.Button(cache_frame, text=BTN_CLEAR_CACHE,
                   command=self._clear_cache).pack(side='left')
        ttk.Label(cache_frame, text=LBL_CACHE_NOTE).pack(
            side='left', padx=(10, 0))

        btn_frame = ttk.Frame(outer)
        btn_frame.pack(fill='x', pady=(8, 0))

        def on_ok():
            self._font_size_pref = size_var.get()
            self._save_font_preference(self._font_size_pref)
            self._save_theme_preference(theme_var.get())
            try:
                self.top_n.set(max(1, int(top_n_var.get())))
                self._config.set_top_n(self.top_n.get())
            except (tk.TclError, ValueError):
                pass
            try:
                self.max_depth.set(max(1, int(max_depth_var.get())))
                self._config.set_max_depth(self.max_depth.get())
            except (tk.TclError, ValueError):
                pass
            try:
                threshold = min(1.0, max(0.0, float(fuzzy_threshold_var.get())))
                self.fuzzy_threshold.set(threshold)
                self._config.set_fuzzy_threshold(threshold)
            except (tk.TclError, ValueError):
                pass
            self.show_ids.set(show_ids_var.get())
            self._config.set_show_ids(show_ids_var.get())
            self._name_order = name_order_var.get()
            self._config.set_name_order(self._name_order)
            self._populate_tree()
            self._refresh_result()
            win.destroy()

        def on_cancel():
            self._apply_font_size(original_font)
            self._apply_theme(original_theme)
            self.top_n.set(original_top_n)
            self.max_depth.set(original_max_depth)
            self.fuzzy_threshold.set(original_fuzzy_threshold)
            win.destroy()

        win.bind('<Escape>', lambda _: on_cancel())
        win.bind('<Return>', lambda _: on_ok())

        ttk.Button(btn_frame, text=BTN_OK, command=on_ok).pack(
            side='right', padx=(4, 0))
        ttk.Button(btn_frame, text=BTN_CANCEL,
                   command=on_cancel).pack(side='right')

        # Size and centre after all widgets are built so the window fits
        # whatever font is currently active (small / medium / large).
        win.update_idletasks()
        req_w = win.winfo_reqwidth()
        req_h = win.winfo_reqheight()
        self.root.update_idletasks()
        px = self.root.winfo_x() + (self.root.winfo_width() - req_w) // 2
        py = self.root.winfo_y() + (self.root.winfo_height() - req_h) // 2
        win.geometry(f"{req_w}x{req_h}+{px}+{py}")
        win.deiconify()

    def _load_show_person_geometry(self):
        """Load the saved geometry for the person detail window."""
        return self._config.get_window_geometry('show_person_geometry')

    def _persist_show_person_geometry(self, win):
        """Persist the current person detail window geometry."""
        try:
            geo = win.geometry()
            self._show_person_geometry = geo
            self._config.set_window_geometry('show_person_geometry', geo)
        except Exception:
            pass

    def _set_home_person(self):
        """Save the selected person as the home person for the active GEDCOM."""
        if not self.individuals:
            messagebox.showwarning(ERR_NO_DATA_TITLE, ERR_NO_DATA_MSG)
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(ERR_NO_SEL_TITLE, ERR_NO_SEL_MSG)
            return
        indi_id = sel[0]
        gedcom_path = self.gedcom_path.get().strip()
        if not gedcom_path:
            return
        self._home_person_id = indi_id
        self._save_home_person(gedcom_path, indi_id)
        name = self.individuals[indi_id]['name'] or indi_id
        self.status_text.set(STATUS_HOME_SET.format(name=name))

    # ---------------------------------------------------------- Keybindings
    def _setup_keybindings(self):
        """Register keyboard shortcuts and focus traversal for the main window."""
        def bind(seq, cmd):
            self.root.bind(seq, lambda _: cmd() or 'break')

        bind('<Control-f>', self._kb_focus_search)
        bind('<Control-i>', self._kb_focus_filter)
        bind('<Control-d>', lambda: self.show_flagged_only.set(
            not self.show_flagged_only.get()))
        bind('<Control-u>', lambda: self.fuzzy_search.set(
            not self.fuzzy_search.get()))
        bind('<Control-p>', self._find_path)
        bind('<Control-t>', self._view_tags)
        bind('<Control-o>', self._browse)
        bind('<Control-h>', self._set_home_person)
        bind('<Control-s>', self._show_person)
        bind('<Control-n>', self._find_matches)
        bind('<Control-l>', self._clear_results)
        bind('<Escape>', self._clear_results)
        # Ctrl-C: only invoke _copy_results when a Text widget isn't focused
        # (Text widgets capture Ctrl-C themselves to copy selected text).
        self.root.bind('<Control-c>', self._kb_copy)

        # Explicit tab chain:
        # tree → results → top_n → max_depth → set_home → show_person → find_matches
        self.results.configure(takefocus=True)
        tab_chain = [
            self.tree, self.results,
            self.top_n_spin, self.max_depth_spin,
            self.set_home_btn, self.show_person_btn, self.find_matches_btn,
        ]
        for i, w in enumerate(tab_chain):
            nxt = tab_chain[(i + 1) % len(tab_chain)]
            prv = tab_chain[(i - 1) % len(tab_chain)]
            w.bind('<Tab>', lambda _, nw=nxt: nw.focus_set() or 'break')
            w.bind('<Shift-Tab>', lambda _, pw=prv: pw.focus_set() or 'break')

        self.root.bind('<Alt-m>', lambda _: self._open_app_menu() or 'break')
        self.root.bind('<Alt-M>', lambda _: self._open_app_menu() or 'break')

        r = self.results
        r.bind('<Up>', lambda _: r.yview_scroll(-1, 'units') or 'break')
        r.bind('<Down>', lambda _: r.yview_scroll(1, 'units') or 'break')
        r.bind('<Prior>', lambda _: r.yview_scroll(-1, 'pages') or 'break')
        r.bind('<Next>', lambda _: r.yview_scroll(1, 'pages') or 'break')
        r.bind('<Home>', lambda _: r.yview_moveto(0) or 'break')
        r.bind('<End>', lambda _: r.yview_moveto(1) or 'break')

    def _open_app_menu(self):
        """Post the application menu at the top-left of the root window."""
        self.root.update_idletasks()
        x = self.root.winfo_rootx()
        y = self.root.winfo_rooty()
        self._app_menu.post(x, y)

    def _kb_focus_search(self):
        """Focus and select the main search field."""
        self.search_entry.focus_set()
        self.search_entry.select_range(0, 'end')

    def _kb_focus_filter(self):
        """Focus and select the raw GEDCOM filter field."""
        self.filter_entry.focus_set()
        self.filter_entry.select_range(0, 'end')

    def _kb_focus_list(self):
        """Focus the people list and select the first row when needed."""
        self.tree.focus_set()
        if not self.tree.focus():
            children = self.tree.get_children()
            if children:
                self.tree.focus(children[0])
                self.tree.selection_set(children[0])

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

    def _tree_jump(self, end, tree=None):
        """Move selection to the first or last row of a tree widget."""
        t = tree or self.tree
        children = t.get_children()
        if not children:
            return
        item = children[0] if end == 'first' else children[-1]
        t.focus_set()
        t.focus(item)
        t.selection_set(item)
        t.see(item)

    def _tree_type_ahead(self, event, tree=None):
        """Select the first tree row whose name starts with the typed character."""
        char = event.char
        if not char or not char.isalnum():
            return
        t = tree or self.tree
        char_lower = char.lower()
        children = t.get_children()
        if not children:
            return
        for item in children:
            name = t.set(item, 'name')
            if name.lower().startswith(char_lower):
                t.focus_set()
                t.focus(item)
                t.selection_set(item)
                t.see(item)
                return 'break'

    def _kb_copy(self, *_):
        """Handle Ctrl-C by copying results unless a Text widget has focus."""
        if isinstance(self.root.focus_get(), tk.Text):
            return  # let the text widget handle its own copy
        self._copy_results()
        return 'break'

    # ---------------------------------------------------------- Menu
    def _setup_menu(self):
        """Build the application menu and connect menu commands."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        self._menubar = menubar

        app_menu = tk.Menu(menubar, tearoff=0)
        self._app_menu = app_menu
        menubar.add_cascade(label=MENU_MENU, underline=0, menu=app_menu)
        app_menu.add_command(label=MENU_PREFERENCES, underline=0,
                             command=self._show_preferences)
        app_menu.add_command(label=MENU_CLEAR_CACHE, underline=0,
                             command=self._clear_cache)
        app_menu.add_separator()
        app_menu.add_command(label=MENU_HOW_TO_USE, underline=0,
                             command=self._show_how_to_use)
        app_menu.add_command(label=MENU_KEYBOARD_SHORTCUTS, underline=0,
                             command=self._show_keyboard_shortcuts)
        app_menu.add_command(label=MENU_PRIVACY_POLICY, underline=1,
                             command=self._show_privacy_policy)
        app_menu.add_command(label=MENU_ABOUT, underline=0,
                             command=self._show_about)

        # macOS supplies Quit via Cmd+Q automatically; only add it explicitly elsewhere.
        if sys.platform != 'darwin':
            app_menu.add_separator()
            app_menu.add_command(label=MENU_QUIT, underline=0,
                                 command=self.root.quit)
        else:
            self.root.createcommand('::tk::mac::Quit', self.root.quit)

    def _resource_path(self, filename):
        """Locate a bundled resource whether running from source or PyInstaller."""
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            # Assume resources are in the parent directory of the script
            # (e.g. in a 'resources' folder), for source version only
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, filename)

    def _show_how_to_use(self):
        """Open the help documentation window."""
        self._show_file_window(
            WIN_HOW_TO_USE, self._resource_path('docs/HELP.md'), markdown=True)

    def _show_keyboard_shortcuts(self):
        """Open the keyboard shortcuts documentation window."""
        self._show_file_window(
            WIN_KEYBOARD_SHORTCUTS,
            self._resource_path('docs/KEYBOARD_SHORTCUTS.md'), markdown=True)

    def _show_about(self):
        """Open the about window with version and license information."""
        self._show_file_window(
            WIN_ABOUT,
            self._resource_path('docs/LICENSE.md'), markdown=True,
            preamble=f"# {APP_TITLE}  v{__version__} ({__release_date__})\n\n",
        )

    def _show_privacy_policy(self):
        """Open the privacy policy documentation window."""
        self._show_file_window(
            WIN_PRIVACY_POLICY,
            self._resource_path('docs/PRIVACY_POLICY.md'), markdown=True,
        )

    def _show_file_window(self, title, filepath, markdown=False, preamble=""):
        """Open a modal text window for a bundled documentation file."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = preamble + f.read()
        except OSError as e:
            messagebox.showerror(
                ERR_FILE_NOT_FOUND_TITLE,
                ERR_FILE_NOT_FOUND_MSG.format(path=filepath, error=e))
            return

        win = tk.Toplevel(self.root)
        win.title(title)
        win.minsize(500, 300)
        win.transient(self.root)
        win.grab_set()
        win.bind('<Escape>', lambda _: win.destroy())
        dw, dh = 820, 640
        self.root.update_idletasks()
        px = self.root.winfo_x() + (self.root.winfo_width() - dw) // 2
        py = self.root.winfo_y() + (self.root.winfo_height() - dh) // 2
        win.geometry(f"{dw}x{dh}+{px}+{py}")

        text = scrolledtext.ScrolledText(win, wrap='word', padx=12, pady=8)
        text.pack(fill='both', expand=True)

        if markdown:
            self._render_markdown(text, content)
        else:
            text.insert('1.0', content)

        text.configure(state='disabled')
        ttk.Button(win, text=BTN_CLOSE, command=win.destroy).pack(pady=(4, 8))

        win.bind('<Up>', lambda _: text.yview_scroll(-1, 'units') or 'break')
        win.bind('<Down>', lambda _: text.yview_scroll(1, 'units') or 'break')
        win.bind('<Prior>', lambda _: text.yview_scroll(-1, 'pages') or 'break')
        win.bind('<Next>', lambda _: text.yview_scroll(1, 'pages') or 'break')
        win.bind('<Home>', lambda _: text.yview_moveto(0) or 'break')
        win.bind('<End>', lambda _: text.yview_moveto(1) or 'break')

        win.lift()
        win.focus_force()

    def _render_markdown(self, widget, content):
        """Render basic markdown into a tkinter Text widget using tag formatting."""
        base = tkfont.Font(font=widget.cget('font'))
        info = base.actual()
        family = info['family']
        size = abs(info['size']) or 10

        widget.tag_configure('h1', font=(
            family, size + 7, 'bold'), spacing1=10, spacing3=5)
        widget.tag_configure('h2', font=(
            family, size + 4, 'bold'), spacing1=8, spacing3=4)
        widget.tag_configure('h3', font=(
            family, size + 2, 'bold'), spacing1=6, spacing3=3)
        widget.tag_configure('bold', font=(family, size, 'bold'))
        widget.tag_configure('italic', font=(family, size, 'italic'))
        widget.tag_configure('code_inline', font=(
            'Courier', size - 1), background='#f0f0f0')
        widget.tag_configure('code_block', font=('Courier', size - 1), background='#f0f0f0',
                             lmargin1=16, lmargin2=16, spacing1=1, spacing3=1)
        widget.tag_configure('link', foreground=self._link_color)
        widget.tag_configure('bullet', lmargin1=16, lmargin2=32)
        widget.tag_configure('normal', font=(family, size))
        widget.tag_configure('table_cell', font=('Courier', size - 1))
        widget.tag_configure('table_bold', font=('Courier', size - 1, 'bold'))

        lines = content.split('\n')

        # Pre-scan: compute max visual column widths across all table rows
        _col_widths: list = []
        for _ln in lines:
            _s = _ln.strip()
            if (_s.startswith('|') and _s.endswith('|')
                    and not re.match(r'^\|[\s\-:|]+\|$', _s)):
                _cells = [c.strip() for c in _s[1:-1].split('|')]
                for _j, _cell in enumerate(_cells):
                    _vl = _visual_len(_cell)
                    if _j >= len(_col_widths):
                        _col_widths.append(_vl)
                    else:
                        _col_widths[_j] = max(_col_widths[_j], _vl)
        # Divider width: │ sp col sp │ sp col sp │ …  = sum(widths) + 3*n + 1
        _divider_width = (sum(_col_widths) + 3 * len(_col_widths) + 1
                          if _col_widths else 64)

        i = 0
        in_code = False
        code_acc = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Fenced code block toggle
            if stripped.startswith('```'):
                if in_code:
                    widget.insert('end', '\n'.join(
                        code_acc) + '\n', 'code_block')
                    code_acc = []
                    in_code = False
                else:
                    in_code = True
                i += 1
                continue

            if in_code:
                code_acc.append(line)
                i += 1
                continue

            # ASCII-art table border row (+---+---+ or +===+===+)
            if re.match(r'^\+[-=+]+\+$', stripped):
                widget.insert('end', '─' * _divider_width + '\n', 'table_cell')
                i += 1
                continue

            # GFM table separator row – skip
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                i += 1
                continue

            # ATX headers (up to ###)
            hm = re.match(r'^(#{1,3})\s+(.*)', stripped)
            if hm:
                self._insert_inline(widget, hm.group(
                    2), 'h' + str(len(hm.group(1))))
                widget.insert('end', '\n')
                i += 1
                continue

            # Horizontal rule
            if re.match(r'^[-*_]{3,}\s*$', stripped):
                widget.insert('end', '─' * 64 + '\n', 'normal')
                i += 1
                continue

            # Table row
            if stripped.startswith('|') and stripped.endswith('|'):
                cells = [c.strip() for c in stripped[1:-1].split('|')]
                next_line = lines[i + 1].strip() if i + 1 < len(lines) else ''
                is_header = bool(
                    re.match(r'^\|[\s\-:|]+\|$', next_line) or
                    re.match(r'^\+[=+]+\+$', next_line)
                )
                base_tag = 'table_bold' if is_header else 'table_cell'
                widget.insert('end', '│ ', base_tag)
                for j, cell in enumerate(cells):
                    self._insert_inline(
                        widget, cell, base_tag, bold_tag='table_bold')
                    pad = (_col_widths[j] - _visual_len(cell)
                           if j < len(_col_widths) else 0)
                    suffix = ' ' * max(0, pad) + ' │'
                    if j < len(cells) - 1:
                        suffix += ' '
                    widget.insert('end', suffix, base_tag)
                widget.insert('end', '\n')
                i += 1
                continue

            # Bullet list
            bm = re.match(r'^[-*+]\s+(.*)', stripped)
            if bm:
                self._insert_inline(widget, '• ' + bm.group(1), 'bullet')
                widget.insert('end', '\n')
                i += 1
                continue

            # Numbered list
            nm = re.match(r'^(\d+\.)\s+(.*)', stripped)
            if nm:
                self._insert_inline(widget, nm.group(
                    1) + ' ' + nm.group(2), 'bullet')
                widget.insert('end', '\n')
                i += 1
                continue

            # Empty line
            if not stripped:
                widget.insert('end', '\n')
                i += 1
                continue

            # Normal paragraph line
            self._insert_inline(widget, line, 'normal')
            widget.insert('end', '\n')
            i += 1

        if code_acc:
            widget.insert('end', '\n'.join(code_acc) + '\n', 'code_block')

    def _insert_inline(self, widget, text, base_tag, bold_tag='bold'):
        """Insert text with inline markdown (bold, italic, code, links) into widget."""
        pos = 0
        for m in _INLINE_RE.finditer(text):
            if m.start() > pos:
                widget.insert('end', text[pos:m.start()], base_tag)
            g1, g2, g3, g4, g5 = m.group(1), m.group(
                2), m.group(3), m.group(4), m.group(5)
            if g1 is not None:
                url = g2
                lc = getattr(widget, '_link_count', 0)
                widget._link_count = lc + 1
                tag = f'_url_{lc}'
                widget.tag_configure(
                    tag, foreground=self._link_color, underline=True)
                widget.tag_bind(tag, '<Button-1>', lambda _,
                                u=url: webbrowser.open(u))
                widget.tag_bind(
                    tag, '<Enter>', lambda _: widget.config(cursor='hand2'))
                widget.tag_bind(
                    tag, '<Leave>', lambda _: widget.config(cursor=''))
                widget.insert('end', g1, (base_tag, tag))
            elif g3 is not None:
                widget.insert('end', g3, (base_tag, bold_tag))
            elif g4 is not None:
                widget.insert('end', g4, (base_tag, 'italic'))
            elif g5 is not None:
                widget.insert('end', g5, 'code_inline')
            # else: image – discard silently
            pos = m.end()
        if pos < len(text):
            widget.insert('end', text[pos:], base_tag)


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
