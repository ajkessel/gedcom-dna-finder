#!/usr/bin/env python3
"""
find_nearest_dna_match.py

Given a GEDCOM file and a target person, find the closest relative(s)
who are flagged as DNA matches.

Two DNA-flag signals are detected (either is sufficient):

  1. A source-citation PAGE line whose text contains "AncestryDNA Match"
     (the format Ancestry uses when you tag a person as a DNA match in
     an Ancestry-managed tree, e.g.
       2 PAGE AncestryDNA Match to Nada Weine Bernat
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
  python find_nearest_dna_match.py tree.ged --list-tags _

  # List every DNA-flagged person
  python find_nearest_dna_match.py tree.ged --list-flagged _

  # Find the 3 nearest DNA-flagged relatives of a person, by name
  python find_nearest_dna_match.py tree.ged "John Q Smith"

  # Find by exact INDI ID (with or without surrounding @)
  python find_nearest_dna_match.py tree.ged @I1234@
  python find_nearest_dna_match.py tree.ged I1234

  # Tighten the tag filter to "DNA Match" only (exclude DNA Connection etc.)
  python find_nearest_dna_match.py tree.ged "John Smith" --tag-keyword "DNA Match"
"""

import argparse
import re
import sys
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

def find_target(individuals, query):
    q = query.strip()
    if q.startswith('@') and q.endswith('@'):
        return [q] if q in individuals else []
    if re.fullmatch(r'[A-Za-z]+\d+', q):
        candidate = f'@{q}@'
        if candidate in individuals:
            return [candidate]
    q_lower = q.lower()
    return [iid for iid, indi in individuals.items()
            if q_lower in indi['name'].lower()]


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
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Find the nearest DNA-flagged relative(s) to a target person in a GEDCOM tree.'
    )
    parser.add_argument('gedcom', help='Path to the GEDCOM file (.ged).')
    parser.add_argument('target', help='Target: INDI ID (e.g. @I123@ or I123) or a name substring. '
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
    parser.add_argument('--list-tags', action='store_true',
                        help='Print all _MTTAG definitions found in the file and exit.')
    parser.add_argument('--list-flagged', action='store_true',
                        help='Print every individual currently flagged as a DNA match and exit.')
    args = parser.parse_args()

    print(f'Parsing {args.gedcom} ...', file=sys.stderr)
    individuals, families, tag_records = build_model(
        args.gedcom,
        dna_keyword=args.tag_keyword,
        page_marker=args.page_marker,
    )
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

    candidates = find_target(individuals, args.target)
    if not candidates:
        print(f'No individuals match: {args.target!r}', file=sys.stderr)
        sys.exit(1)
    if len(candidates) > 1:
        print(f'Multiple candidates match {args.target!r}:', file=sys.stderr)
        for cid in candidates[:25]:
            print(f'  {describe(individuals[cid])}', file=sys.stderr)
        if len(candidates) > 25:
            print(f'  ... and {len(candidates) - 25} more', file=sys.stderr)
        print('Refine the query, or pass an exact INDI ID.', file=sys.stderr)
        sys.exit(1)

    start_id = candidates[0]
    results = bfs_find_dna_matches(
        start_id, individuals, families,
        top_n=args.top, max_depth=args.max_depth,
    )
    print_result(start_id, individuals, results)


if __name__ == '__main__':
    main()
