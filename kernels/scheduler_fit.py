"""Does this set of pods fit on these nodes, and if not, why not.

The lesson every Kubernetes user learns the hard way is that a cluster can have
plenty of free capacity in total and still refuse to schedule a pod, because
capacity does not pool: a pod runs on one node or not at all. This kernel makes
that concrete, and reports the fragmentation rather than just saying no.

Scheduling here is first-fit in declaration order against a fixed node list.
Real schedulers score nodes and filter on far more than resources, so this is a
model of resource fit alone, and any lesson using it should say so.
"""

NAME = "scheduler_fit"
SUMMARY = ("first-fit placement of pods onto nodes by cpu and memory requests, "
           "reporting what stays Pending and how much capacity is stranded")
TOLERANCE = 0

_NODES_3 = [{"name": "node-a", "cpu_m": 4000, "mem_mi": 8192},
            {"name": "node-b", "cpu_m": 4000, "mem_mi": 8192},
            {"name": "node-c", "cpu_m": 4000, "mem_mi": 8192}]

PROBES = [
    # Everything fits comfortably.
    {"nodes": _NODES_3,
     "pods": [{"name": "web", "cpu_m": 500, "mem_mi": 1024},
              {"name": "api", "cpu_m": 500, "mem_mi": 1024}]},
    # The case worth teaching: 6000m free across the cluster, and a 2000m pod
    # that cannot be placed because no single node has room.
    {"nodes": _NODES_3,
     "pods": [{"name": "a", "cpu_m": 3000, "mem_mi": 2048},
              {"name": "b", "cpu_m": 3000, "mem_mi": 2048},
              {"name": "c", "cpu_m": 3000, "mem_mi": 2048},
              {"name": "batch", "cpu_m": 2000, "mem_mi": 1024}]},
    # Memory binds rather than cpu, so a cpu-only check would place it wrongly.
    {"nodes": _NODES_3,
     "pods": [{"name": "cache", "cpu_m": 100, "mem_mi": 8000},
              {"name": "cache2", "cpu_m": 100, "mem_mi": 8000},
              {"name": "cache3", "cpu_m": 100, "mem_mi": 8000},
              {"name": "cache4", "cpu_m": 100, "mem_mi": 8000}]},
    # Exactly fills a node: the boundary where <= and < disagree.
    {"nodes": [{"name": "only", "cpu_m": 1000, "mem_mi": 1024}],
     "pods": [{"name": "exact", "cpu_m": 1000, "mem_mi": 1024}]},
    # One millicore too large for anywhere.
    {"nodes": [{"name": "only", "cpu_m": 1000, "mem_mi": 1024}],
     "pods": [{"name": "over", "cpu_m": 1001, "mem_mi": 1024}]},
    # No pods at all.
    {"nodes": _NODES_3, "pods": []},
    # A pod larger than any node in the cluster, which is Pending forever
    # rather than Pending until something frees up.
    {"nodes": _NODES_3,
     "pods": [{"name": "huge", "cpu_m": 9000, "mem_mi": 1024}]},
]


def reference(nodes, pods):
    """Place pods first-fit and report what could not be placed.

    Returns:
        placements     {pod: node} for everything scheduled
        pending        pods that did not fit, in order
        unschedulable  pending pods too large for *any* empty node, so waiting
                       will never help
        free_cpu_m     cpu left across the whole cluster
        free_mem_mi    memory left across the whole cluster
        stranded       True when something is Pending purely because capacity
                       does not pool: the cluster has room in total, and some
                       empty node could have held it. A pod too large for any
                       node is *not* stranded, because no amount of
                       defragmenting would ever place it, and calling both
                       cases by one name hides the only actionable difference
                       between them.
    """
    remaining = [{"name": n["name"], "cpu_m": int(n["cpu_m"]), "mem_mi": int(n["mem_mi"])}
                 for n in nodes]
    if not remaining:
        raise ValueError("a cluster needs at least one node")
    capacity = [(int(n["cpu_m"]), int(n["mem_mi"])) for n in nodes]

    placements, pending, unschedulable = {}, [], []
    for pod in pods:
        cpu, mem = int(pod["cpu_m"]), int(pod["mem_mi"])
        if cpu < 0 or mem < 0:
            raise ValueError(f"pod {pod['name']!r} requests a negative amount")
        for node in remaining:
            if node["cpu_m"] >= cpu and node["mem_mi"] >= mem:
                node["cpu_m"] -= cpu
                node["mem_mi"] -= mem
                placements[pod["name"]] = node["name"]
                break
        else:
            pending.append(pod["name"])
            # Too big for an empty node of any size in this cluster: no amount
            # of waiting or eviction will place it.
            if not any(c >= cpu and m >= mem for c, m in capacity):
                unschedulable.append(pod["name"])

    free_cpu = sum(n["cpu_m"] for n in remaining)
    free_mem = sum(n["mem_mi"] for n in remaining)
    by_name = {p["name"]: p for p in pods}
    stranded = any(free_cpu >= int(by_name[p]["cpu_m"])
                   and free_mem >= int(by_name[p]["mem_mi"])
                   and p not in unschedulable
                   for p in pending)

    return {
        "placements": placements,
        "pending": pending,
        "unschedulable": unschedulable,
        "free_cpu_m": free_cpu,
        "free_mem_mi": free_mem,
        "stranded": stranded,
    }
