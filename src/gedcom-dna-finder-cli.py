#!/usr/bin/env python3
"""
gedcom-dna-finder-gui.py

Given a GEDCOM file and a target person, find the closest relative(s)
who are flagged as DNA matches.

Two DNA-flag signals are detected (either is sufficient):

  1. A source-citation PAGE line whose text contains "AncestryDNA Match"
     (the format Ancestry uses when you tag a person as a DNA match in
     an Ancestry-managed tree, e.g.
       2 PAGE AncestryDNA Match to James Q. Smith
     attached at any depth under the individual's record).

  2. An _MTTAG line whose value is a pointer to a tag-record whose NAME
     field matches a configurable keyword (default: "DNA"), e.g.
       1 _MTTAG @T182059@
     where the tag record at top level is something like
       0 @T182059@ _MTTAG
       1 NAME DNA Match

Use --list-tags first to see all tag definitions in your file, and
--list-flagged to see every flagged individual. Then run a normal query.

Pure stdlib; no external dependencies.

Usage examples:

  # Inspect all tag definitions present in your GEDCOM
  python gedcom-dna-finder-cli.py tree.ged --list-tags

  # List every DNA-flagged person
  python gedcom-dna-finder-cli.py tree.ged --list-flagged

  # Find the 3 nearest DNA-flagged relatives of a person, by name
  python gedcom-dna-finder-cli.py tree.ged "John A Smith"

  # Names are tokenized: this matches "John Adam Smith"
  # without needing the middle name.
  python gedcom-dna-finder-cli.py tree.ged "John Smith"

  # Find by exact INDI ID (with or without surrounding @)
  python gedcom-dna-finder-cli.py tree.ged @I1234@
  python gedcom-dna-finder-cli.py tree.ged I1234

  # Tighten the tag filter to "DNA Match" only (exclude DNA Connection etc.)
  python gedcom-dna-finder-cli.py tree.ged "John Smith" --tag-keyword "DNA Match"

  # Fuzzy match (tolerates typos and spelling variants):
  # "John Smth" will still find "John Adam Smith".
  python gedcom-dna-finder-cli.py tree.ged "John Smith" --fuzzy
  python gedcom-dna-finder-cli.py tree.ged "John Smith" --fuzzy --fuzzy-threshold 0.7
"""

import argparse
import difflib
import os
import re
import sys
import tempfile
import zipfile
from collections import deque


# ---------------------------------------------------------------------------
# GEDCOM parsing
# ---------------------------------------------------------------------------

# Captures: level, optional xref (@…@), tag (non-space), optional value (rest)
LINE_RE = re.compile(r'^\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?:\s+(.*?))?\s*$')


def iter_records(path):
    """Yield each top-level GEDCOM record as a list of (level, xref, tag, value)."""
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
    """Parse the GEDCOM and return (individuals, families, tag_records).

    individuals[id] = {
        id, name, sex, famc[], fams[], dna_markers[],
        birth_year, death_year
    }
    families[id]    = {id, husb, wife, chil[]}
    tag_records[id] = name string (from the NAME subrecord of an _MTTAG record)
    """
    records = list(iter_records(gedcom_path))

    individuals = {}
    families = {}
    tag_records = {}

    # Pass 1: collect _MTTAG definitions so we can later resolve references.
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

    # Pass 2: individuals and families
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
                        # Inline form: 1 _MTTAG / 2 NAME DNA Match
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

    # Pass 3: resolve _MTTAG pointer references against the tag dictionary
    dna_kw_l = dna_keyword.lower()
    for indi in individuals.values():
        for ref in indi.pop('_mttag_refs'):
            tag_name = tag_records.get(ref, '')
            if tag_name and dna_kw_l in tag_name.lower():
                indi['dna_markers'].append(
                    f'_MTTAG: {tag_name} ({ref})'
                )

    return individuals, families, tag_records


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------

def neighbors(indi_id, individuals, families):
    """Yield (neighbor_id, edge_label) for one BFS step.

    Edges:
      father / mother  via FAMC (parents)
      sibling          via FAMC (other children of the same family)
      spouse           via FAMS (the other partner)
      child            via FAMS (children of this person)
    """
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
    """Return a list of (distance, path) for the nearest DNA-flagged people.

    The BFS continues through DNA-flagged nodes, so a flagged person
    a few hops past another flagged person can still be discovered.
    """
    if start_id not in individuals:
        return []

    # predecessor[node] = (predecessor_id, edge_label_into_node)
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


# ---------------------------------------------------------------------------
# Lookup and output
# ---------------------------------------------------------------------------

def find_target(individuals, query, fuzzy=False, fuzzy_threshold=0.6, fuzzy_max=30):
    """Return a ranked list of (indi_id, score) candidates for `query`.

    Score is None for exact-intent matches (INDI ID lookup or token match).
    Score is a float in [0, 1] for fuzzy matches (only present when
    `fuzzy=True`). Token matches are listed first, then fuzzy matches by
    descending score.

    Name matching is whitespace-tokenized and order-independent: the query
    "John Smith" matches "John Adam Smith", and so does
    "smith john". Each token is a case-insensitive substring match,
    so partial tokens like "Smith" also work.
    """
    q = query.strip()

    # Direct INDI ID lookups (unaffected by tokenization or fuzzy).
    if q.startswith('@') and q.endswith('@'):
        return [(q, None)] if q in individuals else []
    if re.fullmatch(r'[A-Za-z]+\d+', q):
        candidate = f'@{q}@'
        if candidate in individuals:
            return [(candidate, None)]

    q_lower = q.lower()
    tokens = q_lower.split()
    if not tokens:
        return []

    # Token match: every whitespace-separated token must appear (as a
    # substring) somewhere in at least one of the person's names, in any order.
    def _token_match(indi):
        names = indi['alt_names'] or [indi['name']]
        return any(all(tok in name.lower() for tok in tokens) for name in names)

    token_matches = [iid for iid, indi in individuals.items()
                     if _token_match(indi)]
    token_matches.sort(key=lambda iid: individuals[iid]['name'].lower())

    if not fuzzy:
        return [(iid, None) for iid in token_matches]

    # Fuzzy: add anything similar enough that wasn't already a token match.
    # SequenceMatcher with seq2 set once is faster (it caches b2j on seq2).
    token_match_set = set(token_matches)
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(q_lower)
    fuzzy_candidates = []
    for iid, indi in individuals.items():
        if iid in token_match_set:
            continue
        names = indi['alt_names'] or ([indi['name']] if indi['name'] else [])
        if not names:
            continue
        best_score = 0.0
        for name in names:
            matcher.set_seq1(name.lower())
            if matcher.quick_ratio() < fuzzy_threshold:
                continue
            score = matcher.ratio()
            if score > best_score:
                best_score = score
        if best_score >= fuzzy_threshold:
            fuzzy_candidates.append((best_score, iid))

    fuzzy_candidates.sort(
        key=lambda x: (-x[0], individuals[x[1]]['name'].lower()))
    fuzzy_candidates = fuzzy_candidates[:fuzzy_max]

    result = [(iid, None) for iid in token_matches]
    result.extend((iid, score) for score, iid in fuzzy_candidates)
    return result


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


def print_result(start_id, individuals, results):
    start = individuals[start_id]
    print()
    print(f'Starting from: {describe(start)}')
    if start['dna_markers']:
        print('  Note: this person is themselves DNA-flagged.')
        for m in start['dna_markers']:
            print(f'    - {m}')
    print()
    if not results:
        print('No DNA-flagged relatives found within the search depth.')
        return
    for rank, (dist, path) in enumerate(results, 1):
        end_id = path[-1][0]
        end = individuals[end_id]
        print(f'#{rank}: {describe(end)}    (distance: {dist} edges)')
        print('   DNA markers:')
        for m in end['dna_markers']:
            print(f'     - {m}')
        print('   Path:')
        for i, (node_id, edge) in enumerate(path):
            indi = individuals[node_id]
            if i == 0:
                print(f'     {describe(indi)}')
            else:
                print(f'       --[{edge}]--> {describe(indi)}')
        print()


# ---------------------------------------------------------------------------
# ZIP support
# ---------------------------------------------------------------------------

def extract_ged_from_zip(zip_path):
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Find the nearest DNA-flagged relative(s) to a target person in a GEDCOM tree.'
    )
    parser.add_argument('gedcom', help='Path to the GEDCOM file (.ged).')
    parser.add_argument('target', help='Target: INDI ID (e.g. @I123@ or I123) or a name. '
                                       'Names are matched by whitespace-separated tokens, '
                                       'each as a case-insensitive substring, in any order — '
                                       'so "John Smith" matches "John Adam Smith". '
                                       'Use "_" as a placeholder when combined with --list-tags or --list-flagged.')
    parser.add_argument('--top', type=int, default=3,
                        help='Number of nearest matches to return (default 3).')
    parser.add_argument('--max-depth', type=int, default=50,
                        help='Maximum BFS depth in edges (default 50).')
    parser.add_argument('--page-marker', default='AncestryDNA Match',
                        help='Case-insensitive substring to match in source-citation PAGE text. '
                             'Default: "AncestryDNA Match".')
    parser.add_argument('--tag-keyword', default='DNA',
                        help='Case-insensitive substring to match in _MTTAG NAME values. '
                             'Default: "DNA". Use "DNA Match" to exclude DNA Connection / Common DNA Ancestor.')
    parser.add_argument('--fuzzy', action='store_true',
                        help='Enable fuzzy name matching. In addition to token matches, '
                             'include names whose similarity to the query exceeds '
                             '--fuzzy-threshold. Useful for typos and spelling variants.')
    parser.add_argument('--fuzzy-threshold', type=float, default=0.6,
                        help='Similarity cutoff for --fuzzy, between 0.0 and 1.0 (default 0.6). '
                             'Lower = more matches, higher = stricter. '
                             'Uses difflib.SequenceMatcher.ratio.')
    parser.add_argument('--list-tags', action='store_true',
                        help='Print all _MTTAG definitions found in the file and exit.')
    parser.add_argument('--list-flagged', action='store_true',
                        help='Print every individual currently flagged as a DNA match and exit.')
    args = parser.parse_args()

    gedcom_path = args.gedcom
    tmp_path = None
    if gedcom_path.lower().endswith('.zip'):
        try:
            tmp_path, ged_name = extract_ged_from_zip(gedcom_path)
            print(f'Extracted {ged_name!r} from ZIP.', file=sys.stderr)
            gedcom_path = tmp_path
        except Exception as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)

    print(f'Parsing {args.gedcom} ...', file=sys.stderr)
    try:
        individuals, families, tag_records = build_model(
            gedcom_path,
            dna_keyword=args.tag_keyword,
            page_marker=args.page_marker,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    print(f'  {len(individuals)} individuals, {len(families)} families, '
          f'{len(tag_records)} _MTTAG definitions', file=sys.stderr)

    flagged = [i for i in individuals.values() if i['dna_markers']]
    print(f'  {len(flagged)} DNA-flagged individuals', file=sys.stderr)

    if args.list_tags:
        if not tag_records:
            print('No _MTTAG records found.')
        else:
            for tid, name in sorted(tag_records.items()):
                print(f'{tid}\t{name}')
        return

    if args.list_flagged:
        for indi in sorted(flagged, key=lambda x: x['name'].lower()):
            print(describe(indi))
            for m in indi['dna_markers']:
                print(f'  - {m}')
        return

    candidates = find_target(
        individuals, args.target,
        fuzzy=args.fuzzy,
        fuzzy_threshold=args.fuzzy_threshold,
    )
    if not candidates:
        msg = f'No individuals match: {args.target!r}'
        if not args.fuzzy:
            msg += '  (try --fuzzy to allow typos and spelling variants)'
        print(msg, file=sys.stderr)
        sys.exit(1)
    if len(candidates) > 1:
        print(f'Multiple candidates match {args.target!r}:', file=sys.stderr)
        for cid, score in candidates[:25]:
            score_str = f'  [fuzzy: {score:.2f}]' if score is not None else ''
            print(
                f'  {describe(individuals[cid])}{score_str}', file=sys.stderr)
        if len(candidates) > 25:
            print(f'  ... and {len(candidates) - 25} more', file=sys.stderr)
        print('Refine the query, or pass an exact INDI ID.', file=sys.stderr)
        sys.exit(1)

    start_id = candidates[0][0]
    results = bfs_find_dna_matches(
        start_id, individuals, families,
        top_n=args.top, max_depth=args.max_depth,
    )
    print_result(start_id, individuals, results)


if __name__ == '__main__':
    main()
