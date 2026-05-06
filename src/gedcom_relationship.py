"""
gedcom_relationship.py

Pure-Python helpers for extracting GEDCOM events and generating plain-English
relationship descriptions from BFS paths.  No tkinter dependency.
"""

from collections import deque


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
    """Return 'great-' for n==1, '2nd-great-' for n==2, etc.  n==0 returns ''."""
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
        """Split at first *internal* spouse crossing and describe each part."""
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
        no_sp = [e for e in inner if e != 'spouse']
        if no_sp != inner:
            u, d, s, valid = _classify(no_sp)
        else:
            no_sp = inner
        if not valid and no_sp and no_sp[-1] == 'sibling':
            trimmed = no_sp[:-1]
            if trimmed:
                u, d, s, valid = _classify(trimmed)
    if not valid:
        return segmented() or chain()

    u_eff = u + s
    d_eff = d + s

    if d_eff == 0:
        return (ancestor_term(u, target_sex) + '-in-law' if lead_sp
                else 'step-' + ancestor_term(u, target_sex))

    if u_eff == 0:
        return (descendant_term(d, target_sex) + '-in-law' if trail_sp
                else 'step-' + descendant_term(d, target_sex))

    cn = min(u_eff, d_eff) - 1
    rem = abs(u_eff - d_eff)
    more_desc = d_eff > u_eff

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
