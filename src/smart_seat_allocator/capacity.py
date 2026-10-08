"""Necessary infant/caregiver capacity certificates before expensive search."""

from collections import defaultdict
from functools import lru_cache


def infant_capacity(inst):
    fixed = {p.fixed_seat for p in inst.passengers.values() if p.fixed_seat}
    selected = defaultdict(list)
    for group in inst.groups:
        if any(p.fixed_seat for p in group.caregivers):
            continue
        babies = [
            p
            for p in group.passengers
            if p.is_infant and p.needs_caregiver and not p.fixed_seat
        ]
        if babies:
            selected[group.cabin].append(babies[0])
    for cabin, babies in selected.items():
        edges = set()
        for baby in babies:
            for s in baby.domain:
                if s in fixed:
                    continue
                for t in inst.caregiver_neighbors(baby, s):
                    if t not in fixed and inst.cabin_of_seat[t] == cabin:
                        edges.add(tuple(sorted((s, t))))
        adjacency = defaultdict(set)
        for a, b in edges:
            adjacency[a].add(b)
            adjacency[b].add(a)
        unseen, maximum = set(adjacency), 0
        while unseen:
            component, frontier = set(), [min(unseen)]
            while frontier:
                v = frontier.pop()
                if v not in component:
                    component.add(v)
                    frontier.extend(adjacency[v] - component)
            unseen -= component
            vertices = sorted(component)
            masks = [
                sum(1 << j for j, b in enumerate(vertices) if b in adjacency[a])
                for a in vertices
            ]

            @lru_cache(None)
            def matching(mask, masks=tuple(masks)):
                if not mask:
                    return 0
                low = mask & -mask
                i = low.bit_length() - 1
                rest = mask ^ low
                best = matching(rest)
                neighbors = masks[i] & rest
                while neighbors:
                    bit = neighbors & -neighbors
                    best = max(best, 1 + matching(rest ^ bit))
                    neighbors ^= bit
                return best

            maximum += matching((1 << len(vertices)) - 1)
        if maximum < len(babies):
            return {
                "cabin": cabin,
                "required_distinct_groups": len(babies),
                "maximum_disjoint_pairs": maximum,
                "available_edges": sorted(edges),
                "reason": "The relaxed infant/caregiver seat-pair matching has insufficient capacity.",
            }
    return None
