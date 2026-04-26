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

import argparse
import os
import re
import sys
from collections import deque

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext


# ===========================================================================
# Parsing / BFS engine (identical logic to the CLI script)
# ===========================================================================

LINE_RE = re.compile(r'^\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?:\s+(.*?))?\s*$')


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
                'sex': '',
                'famc': [],
                'fams': [],
                'dna_markers': [],
                'birth_year': None,
                'death_year': None,
                '_mttag_refs': [],
            }
            n = len(rec)
            for i, (level, _xref, tag, value) in enumerate(rec):
                if i == 0:
                    continue
                if level == 1 and tag == 'NAME' and not indi['name']:
                    indi['name'] = value.replace('/', '').strip()
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
                elif tag == 'PAGE' and page_marker_l in value.lower():
                    indi['dna_markers'].append(
                        f'Source citation PAGE: "{value.strip()}"'
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
                indi['dna_markers'].append(f'_MTTAG: {tag_name} ({ref})')

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


# ===========================================================================
# GUI
# ===========================================================================

class DNAMatchFinderApp:
    MAX_LIST_DISPLAY = 2000  # cap visible rows in the people list

    def __init__(self, root):
        self.root = root
        self.root.title("GEDCOM DNA Match Finder")
        self.root.geometry("1100x720")
        self.root.minsize(800, 500)

        # Data state
        self.individuals = {}
        self.families = {}
        self.tag_records = {}
        self.sorted_ids = []  # all IDs sorted by name (computed once after load)

        # UI state
        self.gedcom_path = tk.StringVar()
        self.tag_keyword = tk.StringVar(value="DNA")
        self.page_marker = tk.StringVar(value="AncestryDNA Match")
        self.search_text = tk.StringVar()
        self.show_flagged_only = tk.BooleanVar(value=False)
        self.top_n = tk.IntVar(value=3)
        self.max_depth = tk.IntVar(value=50)
        self.status_text = tk.StringVar(value="No file loaded.")

        self.search_text.trace_add('write', self._on_search_change)
        self.show_flagged_only.trace_add('write', self._on_search_change)
        self._search_after_id = None

        self._build_ui()

    # ---------------------------------------------------------- UI build
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill='both', expand=True)

        # File row
        file_frame = ttk.LabelFrame(outer, text="GEDCOM file", padding=8)
        file_frame.pack(fill='x')
        ttk.Entry(file_frame, textvariable=self.gedcom_path).pack(
            side='left', fill='x', expand=True, padx=(0, 4)
        )
        ttk.Button(file_frame, text="Browse…", command=self._browse).pack(side='left', padx=2)
        ttk.Button(file_frame, text="Load", command=self._load_file).pack(side='left', padx=2)

        # Settings row
        settings_frame = ttk.LabelFrame(outer, text="DNA marker settings (apply on next Load)", padding=8)
        settings_frame.pack(fill='x', pady=(8, 0))
        ttk.Label(settings_frame, text="Tag keyword:").grid(row=0, column=0, sticky='w', padx=(0, 4))
        ttk.Entry(settings_frame, textvariable=self.tag_keyword, width=20).grid(row=0, column=1, padx=(0, 16))
        ttk.Label(settings_frame, text="Page marker:").grid(row=0, column=2, sticky='w', padx=(0, 4))
        ttk.Entry(settings_frame, textvariable=self.page_marker, width=30).grid(row=0, column=3, padx=(0, 16))
        ttk.Button(settings_frame, text="View tag definitions…", command=self._view_tags).grid(row=0, column=4, padx=4)

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

        ysb = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview)
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

        # --- Right pane: results ---
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        results_header = ttk.Frame(right)
        results_header.pack(fill='x')
        ttk.Label(results_header, text="Results:").pack(side='left')
        ttk.Button(results_header, text="Copy", command=self._copy_results).pack(side='right')
        ttk.Button(results_header, text="Clear", command=self._clear_results).pack(side='right', padx=(0, 4))

        self.results = scrolledtext.ScrolledText(
            right, font=('Courier', 10), wrap='word', height=10
        )
        self.results.pack(fill='both', expand=True, pady=(4, 0))
        self.results.configure(state='disabled')

        # Status bar
        status = ttk.Label(outer, textvariable=self.status_text, relief='sunken', anchor='w')
        status.pack(fill='x', pady=(8, 0))

    # ---------------------------------------------------------- Handlers
    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select GEDCOM file",
            filetypes=[("GEDCOM files", "*.ged *.gedcom"), ("All files", "*.*")],
        )
        if path:
            self.gedcom_path.set(path)

    def _load_file(self):
        path = self.gedcom_path.get().strip()
        if not path:
            messagebox.showerror("No file", "Please choose a GEDCOM file first.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("Not found", f"File not found:\n{path}")
            return

        self.status_text.set("Loading…")
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            self.individuals, self.families, self.tag_records = build_model(
                path,
                dna_keyword=self.tag_keyword.get(),
                page_marker=self.page_marker.get(),
            )
        except Exception as e:
            self.root.config(cursor="")
            self.status_text.set("Load failed.")
            messagebox.showerror("Parse error", f"Error reading GEDCOM:\n\n{e}")
            return

        self.sorted_ids = sorted(
            self.individuals.keys(),
            key=lambda iid: (self.individuals[iid]['name'].lower(), iid),
        )
        self.root.config(cursor="")
        self._populate_tree()

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
        flagged_count = sum(1 for i in self.individuals.values() if i['dna_markers'])

        shown = 0
        truncated = False
        for indi_id in self.sorted_ids:
            indi = self.individuals[indi_id]
            if flagged_only and not indi['dna_markers']:
                continue
            if query_tokens:
                name_lower = indi['name'].lower()
                id_lower = indi_id.lower()
                # A row matches if every whitespace-separated token in the
                # query appears somewhere in the name (in any order), OR if
                # the full query is a substring of the INDI ID. The second
                # arm preserves the ability to paste "@I1234@" or "I1234"
                # into the search box and jump to that record.
                if not (
                    all(tok in name_lower for tok in query_tokens)
                    or query in id_lower
                ):
                    continue
            if shown >= self.MAX_LIST_DISPLAY:
                truncated = True
                break
            tags = ('flagged_row',) if indi['dna_markers'] else ()
            flagged_mark = '✓' if indi['dna_markers'] else ''
            self.tree.insert(
                '', 'end', iid=indi_id,
                values=(indi['name'] or '(unknown)', lifespan(indi), flagged_mark, indi_id),
                tags=tags,
            )
            shown += 1

        # Restore selection if still present
        if prev_id and self.tree.exists(prev_id):
            self.tree.selection_set(prev_id)
            self.tree.see(prev_id)

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

    def _find_matches(self):
        if not self.individuals:
            messagebox.showwarning("No data", "Load a GEDCOM file first.")
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select a person from the list first.")
            return
        start_id = sel[0]
        try:
            top_n = int(self.top_n.get())
            max_depth = int(self.max_depth.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Bad value", "Top N and Max depth must be integers.")
            return
        results = bfs_find_dna_matches(
            start_id, self.individuals, self.families,
            top_n=top_n, max_depth=max_depth,
        )
        self._render_results(start_id, results)

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
            lines.append("No DNA-flagged relatives found within the search depth.")
        else:
            for rank, (dist, path) in enumerate(results, 1):
                end_id = path[-1][0]
                end = self.individuals[end_id]
                lines.append(f"#{rank}: {describe(end)}    (distance: {dist} edges)")
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
        text = scrolledtext.ScrolledText(win, font=('Courier', 10), wrap='none')
        text.pack(fill='both', expand=True)
        lines = [f"{tid}\t{name}" for tid, name in sorted(self.tag_records.items())]
        text.insert('1.0', '\n'.join(lines))
        text.configure(state='disabled')


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
