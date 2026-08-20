"""Dependency-graph ordering, the shape Terraform and every build tool uses.

Two things people get wrong and one they do not expect:

  * a valid order is not unique, so any check that compares against one blessed
    sequence is wrong more often than the code it is checking
  * destroy is not "apply backwards" in general, but it *is* the reverse of a
    valid apply order, which is a different and provable statement
  * a cycle is not an error to be reported at the end; it means no order exists
    at all, so the answer is not a partial ordering but a refusal
"""

NAME = "dag_order"
SUMMARY = ("topological order for a dependency graph, with level assignment "
           "and cycle detection")
TOLERANCE = 0

PROBES = [
    # A chain: exactly one valid order.
    {"nodes": ["vpc", "subnet", "instance"],
     "edges": [["vpc", "subnet"], ["subnet", "instance"]]},
    # A fan-out: several valid orders, all at the same level.
    {"nodes": ["vpc", "subnet_a", "subnet_b", "subnet_c"],
     "edges": [["vpc", "subnet_a"], ["vpc", "subnet_b"], ["vpc", "subnet_c"]]},
    # A diamond, where level assignment separates it from a chain.
    {"nodes": ["vpc", "sg", "subnet", "instance"],
     "edges": [["vpc", "sg"], ["vpc", "subnet"], ["sg", "instance"], ["subnet", "instance"]]},
    # Disconnected components, which have no ordering between them at all.
    {"nodes": ["bucket", "table", "vpc", "subnet"],
     "edges": [["vpc", "subnet"]]},
    # No edges: every node is independent and everything is level 0.
    {"nodes": ["a", "b", "c"], "edges": []},
    # A cycle: no order exists.
    {"nodes": ["a", "b", "c"], "edges": [["a", "b"], ["b", "c"], ["c", "a"]]},
    # A cycle with an innocent node hanging off it.
    {"nodes": ["a", "b", "free"], "edges": [["a", "b"], ["b", "a"]]},
    # A node depending on itself, which is the smallest possible cycle and the
    # one a naive implementation forgets.
    {"nodes": ["a", "b"], "edges": [["a", "a"], ["a", "b"]]},
    # Single node.
    {"nodes": ["only"], "edges": []},
]


def reference(nodes, edges):
    """Order `nodes` so every dependency comes before what depends on it.

    An edge [a, b] means a must come before b.

    Returns:
        order        a valid ordering, or [] when a cycle makes one impossible
        levels       {node: depth}, where depth is the longest chain reaching it
        has_cycle    whether any ordering exists at all
        in_cycle     the nodes that could not be ordered, sorted
        width        the largest number of nodes shareable at one level, which
                     is how much of the graph could be worked in parallel
    """
    nodes = list(nodes)
    known = set(nodes)
    for a, b in edges:
        if a not in known or b not in known:
            raise ValueError(f"edge [{a!r}, {b!r}] names a node not in the graph")

    after = {n: [] for n in nodes}
    indegree = {n: 0 for n in nodes}
    for a, b in edges:
        after[a].append(b)
        indegree[b] += 1

    # Kahn's algorithm, taking ready nodes in the order they were declared so
    # the result is deterministic. Determinism is a property of this kernel,
    # not of the domain: any order satisfying the edges is equally correct.
    ready = [n for n in nodes if indegree[n] == 0]
    order, levels = [], {n: 0 for n in nodes}
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in after[node]:
            levels[nxt] = max(levels[nxt], levels[node] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
                # Keep declaration order among newly ready nodes.
                ready.sort(key=nodes.index)

    has_cycle = len(order) != len(nodes)
    in_cycle = sorted(set(nodes) - set(order))
    if has_cycle:
        return {"order": [], "levels": {}, "has_cycle": True,
                "in_cycle": in_cycle, "width": 0}

    by_level = {}
    for node, depth in levels.items():
        by_level.setdefault(depth, []).append(node)
    return {
        "order": order,
        "levels": levels,
        "has_cycle": False,
        "in_cycle": [],
        "width": max(len(group) for group in by_level.values()),
    }
