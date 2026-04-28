#!/usr/bin/env python3
"""
gedcom-dna-finder-gui.py

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

__version__ = "0.0.5"
__release_date__ = "2026-04-27"

import argparse
import heapq
import ctypes
import difflib
import json
import os
import re
import sys
import tempfile
import zipfile
from collections import deque
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import tkinter.font as tkfont


# ===========================================================================
# Parsing / BFS engine (identical logic to the CLI script)
# ===========================================================================

LINE_RE = re.compile(r'^\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?:\s+(.*?))?\s*$')

# Inline markdown: image (skip), link, bold, italic, code
_INLINE_RE = re.compile(
    r'!\[[^\]]*\]\([^)]*\)'      # image – discard, no capture groups
    r'|\[([^\]]+)\]\([^)]+\)'    # link: g1 = display text
    r'|\*\*(.+?)\*\*'            # bold: g2
    r'|\*(.+?)\*'                # italic: g3
    r'|`(.+?)`'                  # inline code: g4
)


def iter_records(path):
    record = []
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            if not line.strip():
                continue
            m = LINE_RE.match(line)
            if not m:
                continue
            level = int(m.group(1))
            xref = m.group(2)
            tag = m.group(3)
            value = m.group(4) or ''
            if level == 0 and record:
                yield record
                record = []
            record.append((level, xref, tag, value))
    if record:
        yield record


def extract_year(date_str):
    m = re.search(r'\b(\d{3,4})\b', date_str or '')
    return int(m.group(1)) if m else None


def build_model(gedcom_path, dna_keyword, page_marker):
    records = list(iter_records(gedcom_path))

    individuals = {}
    families = {}
    tag_records = {}

    for rec in records:
        head_level, head_xref, head_tag, _ = rec[0]
        if head_level != 0 or head_xref is None:
            continue
        if head_tag == '_MTTAG':
            tag_name = ''
            for level, _xref, tag, value in rec[1:]:
                if level == 1 and tag == 'NAME' and not tag_name:
                    tag_name = value.strip()
            tag_records[head_xref] = tag_name

    page_marker_l = page_marker.lower()

    for rec in records:
        head_level, head_xref, head_tag, _ = rec[0]
        if head_level != 0 or head_xref is None:
            continue

        if head_tag == 'INDI':
            indi = {
                'id': head_xref,
                'name': '',
                'alt_names': [],
                'sex': '',
                'famc': [],
                'fams': [],
                'dna_markers': [],
                'birth_year': None,
                'death_year': None,
                '_mttag_refs': [],
                '_raw': rec,
            }
            n = len(rec)
            for i, (level, _xref, tag, value) in enumerate(rec):
                if i == 0:
                    continue
                if level == 1 and tag == 'NAME':
                    cleaned = value.replace('/', '').strip()
                    if not indi['name']:
                        indi['name'] = cleaned
                    if cleaned:
                        indi['alt_names'].append(cleaned)
                elif level == 1 and tag == 'SEX':
                    indi['sex'] = value.strip()
                elif level == 1 and tag == 'FAMC':
                    indi['famc'].append(value.strip())
                elif level == 1 and tag == 'FAMS':
                    indi['fams'].append(value.strip())
                elif level == 1 and tag == '_MTTAG':
                    v = value.strip()
                    if v.startswith('@') and v.endswith('@'):
                        indi['_mttag_refs'].append(v)
                    else:
                        for j in range(i + 1, n):
                            l2, _, t2, v2 = rec[j]
                            if l2 <= 1:
                                break
                            if l2 == 2 and t2 == 'NAME':
                                if dna_keyword.lower() in v2.lower():
                                    indi['dna_markers'].append(
                                        f'_MTTAG (inline): {v2.strip()}'
                                    )
                                break
                elif level == 1 and tag in ('BIRT', 'DEAT'):
                    for j in range(i + 1, n):
                        l2, _, t2, v2 = rec[j]
                        if l2 <= 1:
                            break
                        if l2 == 2 and t2 == 'DATE':
                            year = extract_year(v2)
                            if year:
                                if tag == 'BIRT':
                                    indi['birth_year'] = year
                                else:
                                    indi['death_year'] = year
                            break
                elif tag == 'PAGE' and page_marker_l and page_marker_l in value.lower():
                    indi['dna_markers'].append(
                        f'Source citation: "{value.strip()}"'
                    )
            individuals[head_xref] = indi

        elif head_tag == 'FAM':
            fam = {'id': head_xref, 'husb': None, 'wife': None, 'chil': []}
            for level, _xref, tag, value in rec[1:]:
                if level == 1 and tag == 'HUSB':
                    fam['husb'] = value.strip()
                elif level == 1 and tag == 'WIFE':
                    fam['wife'] = value.strip()
                elif level == 1 and tag == 'CHIL':
                    fam['chil'].append(value.strip())
            families[head_xref] = fam

    dna_kw_l = dna_keyword.lower()
    for indi in individuals.values():
        for ref in indi.pop('_mttag_refs'):
            tag_name = tag_records.get(ref, '')
            if tag_name and dna_kw_l in tag_name.lower():
                indi['dna_markers'].append(f'Tags: {tag_name} ({ref})')

    return individuals, families, tag_records


def neighbors(indi_id, individuals, families):
    indi = individuals.get(indi_id)
    if not indi:
        return
    for fam_id in indi['famc']:
        fam = families.get(fam_id)
        if not fam:
            continue
        if fam['husb'] and fam['husb'] != indi_id:
            yield fam['husb'], 'father'
        if fam['wife'] and fam['wife'] != indi_id:
            yield fam['wife'], 'mother'
        for child_id in fam['chil']:
            if child_id != indi_id:
                yield child_id, 'sibling'
    for fam_id in indi['fams']:
        fam = families.get(fam_id)
        if not fam:
            continue
        if fam['husb'] and fam['husb'] != indi_id:
            yield fam['husb'], 'spouse'
        if fam['wife'] and fam['wife'] != indi_id:
            yield fam['wife'], 'spouse'
        for child_id in fam['chil']:
            yield child_id, 'child'


def bfs_find_dna_matches(start_id, individuals, families, top_n, max_depth):
    if start_id not in individuals:
        return []
    predecessor = {start_id: None}
    queue = deque([(start_id, 0)])
    found = []
    while queue:
        current_id, dist = queue.popleft()
        if dist >= max_depth:
            continue
        for neighbor_id, edge_label in neighbors(current_id, individuals, families):
            if neighbor_id in predecessor:
                continue
            predecessor[neighbor_id] = (current_id, edge_label)
            new_dist = dist + 1
            if individuals[neighbor_id]['dna_markers']:
                found.append((new_dist, neighbor_id))
                if len(found) >= top_n:
                    break
            queue.append((neighbor_id, new_dist))
        if len(found) >= top_n:
            break
    results = []
    for dist, end_id in found:
        path = []
        node = end_id
        while node is not None:
            pred = predecessor[node]
            if pred is None:
                path.append((node, None))
                break
            path.append((node, pred[1]))
            node = pred[0]
        path.reverse()
        results.append((dist, path))
    return results


def _is_spouse_detour_of(longer, shorter):
    """Return True if `longer` is `shorter` with one or more spouse-detour nodes inserted.

    A spouse detour is a node S inserted before node N (where N is reached via
    a 'spouse' edge from S) and N already appears in the shorter path. This
    detects e.g. [A, B, grandmother, grandfather] being redundant with
    [A, B, grandfather] when grandmother is grandfather's spouse.
    """
    shorter_ids = {nid for nid, _ in shorter}
    shorter_list = [nid for nid, _ in shorter]
    if longer[0][0] != shorter_list[0] or longer[-1][0] != shorter_list[-1]:
        return False
    if len(longer) <= len(shorter):
        return False
    filtered = []
    i = 0
    while i < len(longer):
        nid, _ = longer[i]
        if nid in shorter_ids:
            filtered.append(nid)
            i += 1
        elif (i + 1 < len(longer)
              and longer[i + 1][1] == 'spouse'
              and longer[i + 1][0] in shorter_ids):
            i += 1  # skip detour node; next iteration picks up the spouse target
        else:
            return False
    return filtered == shorter_list


def _filter_spouse_detours(paths):
    """Remove paths that are spouse-detour variants of a shorter path in the same list."""
    if len(paths) <= 1:
        return paths
    paths = sorted(paths, key=len)
    kept = [paths[0]]
    for candidate in paths[1:]:
        if not any(_is_spouse_detour_of(candidate, keeper) for keeper in kept):
            kept.append(candidate)
    return kept


def bfs_find_all_paths(start_id, end_id, individuals, families, top_n=5, max_depth=50):
    """Find up to top_n distinct paths between start and end.

    Phase 1: standard BFS (O(V+E)) to find the shortest distance.
    Phase 2: path-tracking BFS to collect all simple paths up to
             shortest_distance + 4 edges (or max_depth), whichever is smaller.

    Returns (paths, truncated) where paths is a list of path lists and
    truncated is True when the exploration cap was hit before finishing.
    """
    if start_id not in individuals or end_id not in individuals:
        return [], False
    if start_id == end_id:
        return [[(start_id, None)]], False

    # --- Phase 1: find shortest distance ---
    seen = {start_id}
    q1 = deque([(start_id, 0)])
    shortest = None
    while q1 and shortest is None:
        curr, dist = q1.popleft()
        if dist >= max_depth:
            continue
        for nbr, _ in neighbors(curr, individuals, families):
            if nbr == end_id:
                shortest = dist + 1
                break
            if nbr not in seen:
                seen.add(nbr)
                q1.append((nbr, dist + 1))

    if shortest is None:
        return [], False

    DELTA = 4
    length_limit = min(shortest + DELTA, max_depth)

    # --- Phase 1.5: reverse BFS from end_id to build a distance-to-end map ---
    # Phase 2 uses this to prune branches that cannot reach end_id within the
    # remaining hops, turning an undirected exhaustive search into a directed one.
    dist_to_end = {end_id: 0}
    q_rev = deque([(end_id, 0)])
    while q_rev:
        curr, dist = q_rev.popleft()
        if dist >= length_limit:
            continue
        for nbr, _ in neighbors(curr, individuals, families):
            if nbr not in dist_to_end:
                dist_to_end[nbr] = dist + 1
                q_rev.append((nbr, dist + 1))

    # --- Phase 2: A*-style path search ---
    # Priority = g + h where g = edges used, h = dist_to_end[current].
    # This explores paths in estimated-total-cost order, finding the shortest
    # path in O(branching * depth) rather than the BFS O(branching^depth),
    # which is what caused distant cousins (10+ hops) to exceed MAX_EXPLORE.
    MAX_EXPLORE = 100_000

    found = []
    explored = 0
    truncated = False
    _seq = 0  # tie-breaker so the heap never compares path tuples

    h0 = dist_to_end.get(start_id, length_limit + 1)
    heap = [(h0, _seq, start_id, ((start_id, None),))]

    while heap and len(found) < top_n:
        if explored >= MAX_EXPLORE:
            truncated = True
            break
        _, _, current_id, path = heapq.heappop(heap)
        explored += 1

        g = len(path) - 1  # edges used so far

        path_visited = {nid for nid, _ in path}
        for neighbor_id, edge_label in neighbors(current_id, individuals, families):
            if neighbor_id in path_visited:
                continue
            h = dist_to_end.get(neighbor_id, length_limit + 1)
            new_g = g + 1
            if new_g + h > length_limit:
                continue
            new_path = path + ((neighbor_id, edge_label),)
            if neighbor_id == end_id:
                found.append(list(new_path))
            else:
                _seq += 1
                heapq.heappush(heap, (new_g + h, _seq, neighbor_id, new_path))

    found = _filter_spouse_detours(found)
    return found, truncated


def lifespan(indi):
    b, d = indi.get('birth_year'), indi.get('death_year')
    if b and d:
        return f'{b}-{d}'
    if b:
        return f'b. {b}'
    if d:
        return f'd. {d}'
    return ''


def describe(indi):
    name = indi['name'] or '(unknown)'
    span = lifespan(indi)
    return f'{name} ({span}) [{indi["id"]}]' if span else f'{name} [{indi["id"]}]'


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

    Recognises ancestors, descendants, siblings, spouses, cousins (all degrees
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
        return chain()

    # Validate inner: all-up → optional single sibling → all-down
    state = 'up'
    u = d = s = 0
    valid = True
    for e in inner:
        if state == 'up':
            if e in up_set:
                u += 1
            elif e == 'sibling':
                s += 1
                state = 'down'
            elif e == 'child':
                d += 1
                state = 'down'
            else:
                valid = False
                break
        elif state == 'down':
            if e == 'child':
                d += 1
            else:
                valid = False
                break
    if not valid:
        return chain()

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
# ZIP support
# ===========================================================================

def _extract_ged_from_zip(zip_path):
    """Return (temp_ged_path, entry_name) for the first .ged/.gedcom in a ZIP.

    Prefers top-level entries over those inside subdirectories.
    Caller is responsible for deleting the returned temp file.
    """
    with zipfile.ZipFile(zip_path, 'r') as zf:
        ged_names = sorted(
            [n for n in zf.namelist() if n.lower().endswith(('.ged', '.gedcom'))],
            key=lambda n: (n.count('/'), n.lower()),
        )
        if not ged_names:
            raise ValueError(
                "No .ged or .gedcom file found inside the ZIP archive.")
        chosen = ged_names[0]
        data = zf.read(chosen)
    tmp = tempfile.NamedTemporaryFile(suffix='.ged', delete=False)
    tmp.write(data)
    tmp.close()
    return tmp.name, chosen


# ===========================================================================
# GUI
# ===========================================================================

class DNAMatchFinderApp:
    MAX_LIST_DISPLAY = 2000  # cap visible rows in the people list
    FUZZY_THRESHOLD = 0.72   # minimum SequenceMatcher ratio to count as a match
    MAX_RECENT = 10          # number of recent files to remember

    def __init__(self, root):
        self.root = root
        self.root.title("GEDCOM DNA Match Finder")
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

        # UI state
        self.gedcom_path = tk.StringVar()
        self.tag_keyword = tk.StringVar(value="DNA")
        self.page_marker = tk.StringVar(value="AncestryDNA Match")
        self.search_text = tk.StringVar()
        self.show_flagged_only = tk.BooleanVar(value=False)
        self.top_n = tk.IntVar(value=3)
        self.max_depth = tk.IntVar(value=50)
        self.status_text = tk.StringVar(value="No file loaded.")

        self.fuzzy_search = tk.BooleanVar(value=False)

        self.search_text.trace_add('write', self._on_search_change)
        self.show_flagged_only.trace_add('write', self._on_search_change)
        self.fuzzy_search.trace_add('write', self._on_search_change)
        self._search_after_id = None

        self.top_n.trace_add('write', self._on_settings_change)
        self.max_depth.trace_add('write', self._on_settings_change)
        self._settings_after_id = None
        # {'type': 'dna_matches'|'path', 'start_id': ..., 'end_id': ...}
        self._last_result = None

        self._recent_files = self._load_history()

        self._build_ui()

        # Re-open the most-recently-used file automatically on startup
        if self._recent_files and os.path.isfile(self._recent_files[0]):
            self.gedcom_path.set(self._recent_files[0])
            self.root.after(0, self._load_file)

    # ---------------------------------------------------------- UI build
    def _build_ui(self):
        self._setup_menu()
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill='both', expand=True)

        # File row
        file_frame = ttk.LabelFrame(outer, text="GEDCOM file", padding=8)
        file_frame.pack(fill='x')
        self.path_combo = ttk.Combobox(
            file_frame, textvariable=self.gedcom_path, values=self._recent_files
        )
        self.path_combo.pack(side='left', fill='x', expand=True, padx=(0, 4))
        self.path_combo.bind('<<ComboboxSelected>>',
                             lambda _: self._load_file())
        ttk.Button(file_frame, text="Browse…",
                   command=self._browse).pack(side='left', padx=2)
        ttk.Button(file_frame, text="Load", command=self._load_file).pack(
            side='left', padx=2)

        # Settings row
        settings_frame = ttk.LabelFrame(
            outer, text="DNA marker settings (apply on next Load)", padding=8)
        settings_frame.pack(fill='x', pady=(8, 0))
        ttk.Label(settings_frame, text="Tag keyword:").grid(
            row=0, column=0, sticky='w', padx=(0, 4))
        ttk.Entry(settings_frame, textvariable=self.tag_keyword,
                  width=20).grid(row=0, column=1, padx=(0, 16))
        ttk.Label(settings_frame, text="Page marker:").grid(
            row=0, column=2, sticky='w', padx=(0, 4))
        ttk.Entry(settings_frame, textvariable=self.page_marker,
                  width=30).grid(row=0, column=3, padx=(0, 16))
        ttk.Button(settings_frame, text="View tag definitions…",
                   command=self._view_tags).grid(row=0, column=4, padx=4)
        ttk.Button(settings_frame, text="Find Relationship Path…",
                   command=self._find_path).grid(row=0, column=5, padx=(12, 4))

        # Main paned area
        paned = ttk.PanedWindow(outer, orient='horizontal')
        paned.pack(fill='both', expand=True, pady=(8, 0))

        # --- Left pane: search + list + action controls ---
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        search_frame = ttk.Frame(left)
        search_frame.pack(fill='x')
        ttk.Label(search_frame, text="Search:").pack(side='left', padx=(0, 4))
        ttk.Entry(search_frame, textvariable=self.search_text).pack(
            side='left', fill='x', expand=True
        )
        ttk.Checkbutton(
            search_frame, text="DNA-flagged only", variable=self.show_flagged_only
        ).pack(side='left', padx=(8, 0))
        ttk.Checkbutton(
            search_frame, text="Fuzzy", variable=self.fuzzy_search
        ).pack(side='left', padx=(8, 0))

        list_frame = ttk.Frame(left)
        list_frame.pack(fill='both', expand=True, pady=(4, 0))

        self.tree = ttk.Treeview(
            list_frame,
            columns=('name', 'years', 'flagged', 'id'),
            show='headings',
            selectmode='browse',
        )
        self.tree.heading('name', text='Name')
        self.tree.heading('years', text='Years')
        self.tree.heading('flagged', text='DNA?')
        self.tree.heading('id', text='ID')
        self.tree.column('name', width=260, anchor='w', stretch=True)
        self.tree.column('years', width=80, anchor='w', stretch=False)
        self.tree.column('flagged', width=50, anchor='center', stretch=False)
        self.tree.column('id', width=90, anchor='w', stretch=False)

        ysb = ttk.Scrollbar(list_frame, orient='vertical',
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        ysb.pack(side='right', fill='y')

        # Highlight flagged rows
        self.tree.tag_configure('flagged_row', background='#fff4cc')

        self.tree.bind('<Double-1>', lambda e: self._find_matches())
        self.tree.bind('<Return>', lambda e: self._find_matches())

        # Action controls
        action_frame = ttk.Frame(left)
        action_frame.pack(fill='x', pady=(6, 0))
        ttk.Label(action_frame, text="Top N:").pack(side='left')
        ttk.Spinbox(action_frame, from_=1, to=20, textvariable=self.top_n, width=4).pack(
            side='left', padx=(2, 12)
        )
        ttk.Label(action_frame, text="Max depth:").pack(side='left')
        ttk.Spinbox(action_frame, from_=1, to=200, textvariable=self.max_depth, width=4).pack(
            side='left', padx=(2, 12)
        )
        ttk.Button(
            action_frame, text="Find Nearest DNA Matches", command=self._find_matches
        ).pack(side='right')
        ttk.Button(
            action_frame, text="Show Person", command=self._show_person
        ).pack(side='right', padx=(0, 6))

        # --- Right pane: results ---
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        results_header = ttk.Frame(right)
        results_header.pack(fill='x')
        ttk.Label(results_header, text="Results:").pack(side='left')
        ttk.Button(results_header, text="Copy",
                   command=self._copy_results).pack(side='right')
        ttk.Button(results_header, text="Clear", command=self._clear_results).pack(
            side='right', padx=(0, 4))

        self.results = scrolledtext.ScrolledText(
            right, font=('Courier', 10), wrap='word', height=10
        )
        self.results.pack(fill='both', expand=True, pady=(4, 0))
        self.results.configure(state='disabled')

        # Status bar
        status = ttk.Label(outer, textvariable=self.status_text,
                           relief='sunken', anchor='w')
        status.pack(fill='x', pady=(8, 0))

    # ---------------------------------------------------------- Handlers
    def _browse(self):
        current = self.gedcom_path.get().strip()
        initialdir = os.path.dirname(current) if current else None
        path = filedialog.askopenfilename(
            title="Select GEDCOM file",
            filetypes=[("GEDCOM files", "*.ged *.gedcom *.zip"),
                       ("All files", "*.*")],
            initialdir=initialdir,
        )
        if path:
            self.gedcom_path.set(path)
            self._load_file()

    def _load_file(self):
        path = self.gedcom_path.get().strip()
        if not path:
            messagebox.showerror(
                "No file", "Please choose a GEDCOM file first.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("Not found", f"File not found:\n{path}")
            return

        gedcom_path = path
        tmp_path = None
        if path.lower().endswith('.zip'):
            try:
                tmp_path, ged_name = _extract_ged_from_zip(path)
                gedcom_path = tmp_path
                self.status_text.set(f"Extracted {ged_name} from ZIP…")
            except Exception as e:
                messagebox.showerror(
                    "ZIP error", f"Could not extract GEDCOM from ZIP:\n\n{e}")
                return

        self.status_text.set("Loading…")
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            self.individuals, self.families, self.tag_records = build_model(
                gedcom_path,
                dna_keyword=self.tag_keyword.get(),
                page_marker=self.page_marker.get(),
            )
        except Exception as e:
            self.root.config(cursor="")
            self.status_text.set("Load failed.")
            messagebox.showerror(
                "Parse error", f"Error reading GEDCOM:\n\n{e}")
            return
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        self.sorted_ids = sorted(
            self.individuals.keys(),
            key=lambda iid: (self.individuals[iid]['name'].lower(), iid),
        )
        self.root.config(cursor="")
        self._add_to_history(path)
        self._populate_tree()

    def _on_settings_change(self, *_):
        if self._settings_after_id is not None:
            self.root.after_cancel(self._settings_after_id)
        self._settings_after_id = self.root.after(400, self._refresh_result)

    def _refresh_result(self):
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
            results = bfs_find_dna_matches(
                start_id, self.individuals, self.families,
                top_n=top_n, max_depth=max_depth,
            )
            self._render_results(start_id, results)
        elif kind == 'path':
            end_id = self._last_result['end_id']
            paths, truncated = bfs_find_all_paths(
                start_id, end_id, self.individuals, self.families,
                top_n=top_n, max_depth=max_depth,
            )
            self._render_path_results(start_id, end_id, paths, truncated)

    def _on_search_change(self, *_):
        # Debounce so typing doesn't refilter on every keystroke
        if self._search_after_id is not None:
            self.root.after_cancel(self._search_after_id)
        self._search_after_id = self.root.after(150, self._populate_tree)

    def _populate_tree(self):
        self._search_after_id = None
        # Save current selection so we can restore it if still visible
        prev_sel = self.tree.selection()
        prev_id = prev_sel[0] if prev_sel else None

        self.tree.delete(*self.tree.get_children())

        if not self.individuals:
            return

        query = self.search_text.get().strip().lower()
        query_tokens = query.split()
        flagged_only = self.show_flagged_only.get()
        flagged_count = sum(
            1 for i in self.individuals.values() if i['dna_markers'])

        shown = 0
        truncated = False
        for indi_id in self.sorted_ids:
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
            if shown >= self.MAX_LIST_DISPLAY:
                truncated = True
                break
            tags = ('flagged_row',) if indi['dna_markers'] else ()
            flagged_mark = '✓' if indi['dna_markers'] else ''
            self.tree.insert(
                '', 'end', iid=indi_id,
                values=(indi['name'] or '(unknown)',
                        lifespan(indi), flagged_mark, indi_id),
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
            self.status_text.set(
                f"Showing first {self.MAX_LIST_DISPLAY:,} of more matches. "
                f"Refine your search.  ({total:,} total, {flagged_count} DNA-flagged)"
            )
        elif query or flagged_only:
            self.status_text.set(
                f"{shown:,} match{'es' if shown != 1 else ''} shown.  "
                f"({total:,} total, {flagged_count} DNA-flagged)"
            )
        else:
            self.status_text.set(
                f"{total:,} individuals, {len(self.families):,} families, "
                f"{flagged_count} DNA-flagged.  Type to search."
            )

    def _fuzzy_token_matches(self, token, name_words):
        return any(
            difflib.SequenceMatcher(
                None, token, word).ratio() >= self.FUZZY_THRESHOLD
            for word in name_words
        )

    def _find_matches(self):
        if not self.individuals:
            messagebox.showwarning("No data", "Load a GEDCOM file first.")
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(
                "No selection", "Select a person from the list first.")
            return
        start_id = sel[0]
        try:
            top_n = int(self.top_n.get())
            max_depth = int(self.max_depth.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Bad value", "Top N and Max depth must be integers.")
            return
        results = bfs_find_dna_matches(
            start_id, self.individuals, self.families,
            top_n=top_n, max_depth=max_depth,
        )
        self._last_result = {'type': 'dna_matches', 'start_id': start_id}
        self._render_results(start_id, results)

    def _show_person(self):
        if not self.individuals:
            messagebox.showwarning("No data", "Load a GEDCOM file first.")
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(
                "No selection", "Select a person from the list first.")
            return
        indi_id = sel[0]
        indi = self.individuals[indi_id]

        win = tk.Toplevel(self.root)
        win.title(f"GEDCOM Record: {indi['name'] or indi_id}")
        win.geometry("700x520")
        win.minsize(400, 300)

        text = scrolledtext.ScrolledText(win, font=(
            'Courier', 10), wrap='none', padx=8, pady=8)
        text.pack(fill='both', expand=True)

        lines = []
        for level, xref, tag, value in indi.get('_raw', []):
            parts = [str(level)]
            if xref:
                parts.append(xref)
            parts.append(tag)
            if value:
                parts.append(value)
            lines.append(' '.join(parts))

        text.insert('1.0', '\n'.join(lines))
        text.configure(state='disabled')

        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill='x', pady=(4, 8))
        ttk.Button(btn_frame, text="Close", command=win.destroy).pack(
            side='right', padx=8)

    def _render_results(self, start_id, results):
        start = self.individuals[start_id]
        lines = []
        lines.append(f"Starting from: {describe(start)}")
        if start['dna_markers']:
            lines.append("  Note: this person is themselves DNA-flagged.")
            for m in start['dna_markers']:
                lines.append(f"    - {m}")
        lines.append("")

        if not results:
            lines.append(
                "No DNA-flagged relatives found within the search depth.")
        else:
            for rank, (dist, path) in enumerate(results, 1):
                end_id = path[-1][0]
                end = self.individuals[end_id]
                lines.append(
                    f"#{rank}: {describe(end)}    (distance: {dist} edges)")
                lines.append("   DNA markers:")
                for m in end['dna_markers']:
                    lines.append(f"     - {m}")
                lines.append("   Path:")
                for i, (node_id, edge) in enumerate(path):
                    indi = self.individuals[node_id]
                    if i == 0:
                        lines.append(f"     {describe(indi)}")
                    else:
                        lines.append(f"       --[{edge}]--> {describe(indi)}")
                lines.append("")

        self.results.configure(state='normal')
        self.results.delete('1.0', 'end')
        self.results.insert('1.0', '\n'.join(lines))
        self.results.configure(state='disabled')

    def _copy_results(self):
        text = self.results.get('1.0', 'end').rstrip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _clear_results(self):
        self.results.configure(state='normal')
        self.results.delete('1.0', 'end')
        self.results.configure(state='disabled')

    def _view_tags(self):
        if not self.tag_records:
            messagebox.showinfo("Tag definitions",
                                "No _MTTAG records found in the loaded file.\n\n"
                                "(If you haven't loaded a file yet, click Load first.)")
            return
        win = tk.Toplevel(self.root)
        win.title("_MTTAG definitions")
        win.geometry("450x400")
        text = scrolledtext.ScrolledText(
            win, font=('Courier', 10), wrap='none')
        text.pack(fill='both', expand=True)
        lines = [f"{tid}\t{name}" for tid,
                 name in sorted(self.tag_records.items())]
        text.insert('1.0', '\n'.join(lines))
        text.configure(state='disabled')

    def _pick_person(self, title="Select a Person"):
        """Modal dialog to pick one person from the loaded GEDCOM. Returns indi_id or None."""
        if not self.individuals:
            messagebox.showwarning("No data", "Load a GEDCOM file first.")
            return None

        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()

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
        ttk.Label(search_frame, text="Search:").pack(side='left', padx=(0, 4))
        search_var = tk.StringVar()
        ttk.Entry(search_frame, textvariable=search_var).pack(
            side='left', fill='x', expand=True)

        list_frame = ttk.Frame(dialog, padding=(8, 0, 8, 0))
        list_frame.pack(fill='both', expand=True)

        picker_tree = ttk.Treeview(
            list_frame,
            columns=('name', 'years', 'flagged', 'id'),
            show='headings',
            selectmode='browse',
        )
        picker_tree.heading('name', text='Name')
        picker_tree.heading('years', text='Years')
        picker_tree.heading('flagged', text='DNA?')
        picker_tree.heading('id', text='ID')
        picker_tree.column('name', width=260, anchor='w', stretch=True)
        picker_tree.column('years', width=80, anchor='w', stretch=False)
        picker_tree.column('flagged', width=50, anchor='center', stretch=False)
        picker_tree.column('id', width=90, anchor='w', stretch=False)
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
                    values=(indi['name'] or '(unknown)',
                            lifespan(indi), flagged_mark, indi_id),
                    tags=tags,
                )
                shown += 1
                if shown >= self.MAX_LIST_DISPLAY:
                    break

        def on_search_change(*_):
            if after_id[0]:
                dialog.after_cancel(after_id[0])
            after_id[0] = dialog.after(150, lambda: populate(search_var.get()))

        search_var.trace_add('write', on_search_change)
        populate()

        def select():
            sel = picker_tree.selection()
            if sel:
                result[0] = sel[0]
            dialog.destroy()

        picker_tree.bind('<Double-1>', lambda e: select())
        picker_tree.bind('<Return>', lambda e: select())

        btn_frame = ttk.Frame(dialog, padding=8)
        btn_frame.pack(fill='x')
        ttk.Button(btn_frame, text="Select", command=select).pack(
            side='right', padx=(4, 0))
        ttk.Button(btn_frame, text="Cancel",
                   command=dialog.destroy).pack(side='right')

        dialog.wait_window()
        return result[0]

    def _find_path(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No selection",
                                   "Select a starting person from the main list first.")
            return
        start_id = sel[0]

        target_id = self._pick_person("Select Relationship Target")
        if not target_id:
            return

        try:
            max_depth = int(self.max_depth.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Bad value", "Max depth must be an integer.")
            return

        try:
            top_n = int(self.top_n.get())
        except (tk.TclError, ValueError):
            top_n = 5
        paths, truncated = bfs_find_all_paths(
            start_id, target_id, self.individuals, self.families,
            top_n=top_n, max_depth=max_depth,
        )
        self._last_result = {'type': 'path',
                             'start_id': start_id, 'end_id': target_id}
        self._render_path_results(start_id, target_id, paths, truncated)

    def _render_path_results(self, start_id, end_id, paths, truncated=False):
        start = self.individuals[start_id]
        end = self.individuals[end_id]
        lines = [
            "Relationship path:",
            f"  From: {describe(start)}",
            f"  To:   {describe(end)}",
            "",
        ]
        if start_id == end_id:
            lines.append("(Same person selected for both.)")
        elif not paths:
            lines.append(
                f"No relationship path found within max depth {self.max_depth.get()}."
            )
        else:
            ancestors = get_ancestor_depths(
                start_id, self.individuals, self.families)
            descendants = get_descendant_depths(
                start_id, self.individuals, self.families)
            for rank, path in enumerate(paths, 1):
                dist = len(path) - 1
                rel = describe_relationship(path, self.individuals,
                                            ancestors=ancestors, descendants=descendants)
                lines.append(
                    f"Path #{rank} — {rel} ({dist} edge{'s' if dist != 1 else ''}):")
                for i, (node_id, edge) in enumerate(path):
                    indi = self.individuals[node_id]
                    if i == 0:
                        lines.append(f"  {describe(indi)}")
                    else:
                        lines.append(f"    --[{edge}]--> {describe(indi)}")
                lines.append("")
            if truncated:
                lines.append(
                    "(Search cap reached — there may be additional paths. "
                    "Reduce Max depth to search a smaller area.)"
                )
        lines.append("")

        self.results.configure(state='normal')
        self.results.delete('1.0', 'end')
        self.results.insert('1.0', '\n'.join(lines))
        self.results.configure(state='disabled')

    # ---------------------------------------------------------- History / config
    def _config_path(self):
        if sys.platform == 'win32':
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "com.ajkessel.gedcom-dna-finder")
            base = Path(os.environ.get('APPDATA', Path.home()))
        elif sys.platform == 'darwin':
            base = Path.home() / 'Library' / 'Application Support'
        else:
            base = Path(os.environ.get(
                'XDG_CONFIG_HOME', Path.home() / '.config'))
        return base / 'gedcom-dna-finder' / 'settings.json'

    def _load_history(self):
        try:
            data = json.loads(self._config_path().read_text(encoding='utf-8'))
            return [p for p in data.get('recent_files', []) if isinstance(p, str)]
        except Exception:
            return []

    def _save_history(self, history):
        cfg = self._config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(
            {'recent_files': history}, indent=2), encoding='utf-8')

    def _add_to_history(self, filepath):
        history = [filepath] + [p for p in self._recent_files if p != filepath]
        history = history[:self.MAX_RECENT]
        self._recent_files = history
        self.path_combo['values'] = history
        self._save_history(history)

    # ---------------------------------------------------------- Menu
    def _setup_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        app_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label='Menu', menu=app_menu)
        app_menu.add_command(label='How to use', command=self._show_how_to_use)
        app_menu.add_command(label='About', command=self._show_about)

        # macOS supplies Quit via Cmd+Q automatically; only add it explicitly elsewhere.
        if sys.platform != 'darwin':
            app_menu.add_separator()
            app_menu.add_command(label='Quit', command=self.root.quit)
        else:
            self.root.createcommand('::tk::mac::Quit', self.root.quit)

    def _resource_path(self, filename):
        """Locate a bundled resource whether running from source or PyInstaller."""
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            # Assume resources are in the parent directory of the script (e.g. in a 'resources' folder), for source version only
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, filename)

    def _show_how_to_use(self):
        self._show_file_window(
            "How to use", self._resource_path('docs/HELP.md'), markdown=True)

    def _show_about(self):
        self._show_file_window(
            "About",
            self._resource_path('LICENSE'),
            markdown=False,
            preamble=f"GEDCOM DNA Match Finder  v{__version__} ({__release_date__})\n\n",
        )

    def _show_file_window(self, title, filepath, markdown=False, preamble=""):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = preamble + f.read()
        except OSError as e:
            messagebox.showerror(
                "File not found", f"Could not open:\n{filepath}\n\n{e}")
            return

        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("820x640")
        win.minsize(500, 300)

        text = scrolledtext.ScrolledText(win, wrap='word', padx=12, pady=8)
        text.pack(fill='both', expand=True)

        if markdown:
            self._render_markdown(text, content)
        else:
            text.insert('1.0', content)

        text.configure(state='disabled')
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(4, 8))

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
        widget.tag_configure('link', foreground='#0066cc')
        widget.tag_configure('bullet', lmargin1=16, lmargin2=32)
        widget.tag_configure('normal', font=(family, size))

        lines = content.split('\n')
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

            # Table separator row – skip
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
                is_header = (i + 1 < len(lines) and
                             re.match(r'^\|[\s\-:|]+\|$', lines[i + 1].strip()))
                self._insert_inline(widget, '  '.join(
                    cells), 'bold' if is_header else 'normal')
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

    def _insert_inline(self, widget, text, base_tag):
        """Insert text with inline markdown (bold, italic, code, links) into widget."""
        pos = 0
        for m in _INLINE_RE.finditer(text):
            if m.start() > pos:
                widget.insert('end', text[pos:m.start()], base_tag)
            g1, g2, g3, g4 = m.group(1), m.group(2), m.group(3), m.group(4)
            if g1 is not None:
                widget.insert('end', g1, (base_tag, 'link'))
            elif g2 is not None:
                widget.insert('end', g2, (base_tag, 'bold'))
            elif g3 is not None:
                widget.insert('end', g3, (base_tag, 'italic'))
            elif g4 is not None:
                widget.insert('end', g4, 'code_inline')
            # else: image – discard silently
            pos = m.end()
        if pos < len(text):
            widget.insert('end', text[pos:], base_tag)


def main():
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
                    "File not found",
                    f"GEDCOM file not found:\n{p}\n\n"
                    "Use Browse… to choose a different file."
                ),
            )

    root.mainloop()


if __name__ == '__main__':
    main()
