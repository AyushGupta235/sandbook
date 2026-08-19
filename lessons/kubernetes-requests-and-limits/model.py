"""Model functions for "Requests, Limits & Why Your Pod Is Pending".

Targets Kubernetes 1.29+ behaviour with the default kube-scheduler.

Simplifications, stated honestly because a lesson that quietly lies is worse
than no lesson:
  * Node "allocatable" is treated as the whole node. Real nodes reserve capacity
    for the kubelet and system daemons, so real allocatable is smaller.
  * Scheduling scores nodes by LeastAllocated only. The real scheduler runs a
    filter phase then a weighted set of score plugins, of which
    NodeResourcesFit/LeastAllocated is one.
  * Only CPU and memory are modelled. Real scheduling also considers pod
    affinity, taints/tolerations, topology spread, ephemeral storage and more.
None of these change the behaviour the lesson actually teaches.
"""

import yaml

# Capacity of each worker node, in millicores and MiB.
NODES = [
    {"name": "node-a", "cpu_m": 4000, "mem_mi": 8192},
    {"name": "node-b", "cpu_m": 4000, "mem_mi": 8192},
    {"name": "node-c", "cpu_m": 4000, "mem_mi": 8192},
]

# Deliberately ordered to fragment the cluster.
POD_QUEUE = [
    {"name": "web-1", "cpu_m": 1500, "mem_mi": 2048},
    {"name": "web-2", "cpu_m": 1500, "mem_mi": 2048},
    {"name": "cache-1", "cpu_m": 1000, "mem_mi": 3072},
    {"name": "worker-1", "cpu_m": 2000, "mem_mi": 2048},
    {"name": "batch-1", "cpu_m": 3500, "mem_mi": 1024},
]


# ------------------------------------------------------------------ parsing


def parse_cpu(value):
    """Kubernetes CPU quantity -> millicores. '500m' -> 500, '2' -> 2000."""
    if isinstance(value, (int, float)):
        return int(round(float(value) * 1000))
    s = str(value).strip()
    if not s:
        raise ValueError("empty CPU quantity")
    if s.endswith("m"):
        return int(round(float(s[:-1])))
    return int(round(float(s) * 1000))


def parse_mem(value):
    """Kubernetes memory quantity -> MiB. Accepts Ki/Mi/Gi/K/M/G and bare bytes."""
    if isinstance(value, (int, float)):
        return int(round(float(value) / (1024 * 1024)))
    s = str(value).strip()
    if not s:
        raise ValueError("empty memory quantity")
    units = {"Ki": 1 / 1024, "Mi": 1.0, "Gi": 1024.0, "Ti": 1024.0 * 1024,
             "K": 1000.0 / (1024 * 1024), "M": 1e6 / (1024 * 1024), "G": 1e9 / (1024 * 1024)}
    for suffix, factor in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if s.endswith(suffix):
            return int(round(float(s[: -len(suffix)]) * factor))
    return int(round(float(s) / (1024 * 1024)))


# ---------------------------------------------------------------- QoS class


def qos_class(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi):
    """Kubernetes QoS class. 0 means "not set".

    Guaranteed  : cpu and memory limits set, and requests equal limits
    BestEffort  : nothing set at all
    Burstable   : everything else
    """
    anything_set = any(v > 0 for v in (req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi))
    if not anything_set:
        return "BestEffort"
    limits_set = lim_cpu_m > 0 and lim_mem_mi > 0
    # An omitted request defaults to the limit, which is why 0 counts as equal here.
    cpu_equal = req_cpu_m in (0, lim_cpu_m)
    mem_equal = req_mem_mi in (0, lim_mem_mi)
    if limits_set and cpu_equal and mem_equal:
        return "Guaranteed"
    return "Burstable"


def qos_explanation(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi):
    cls = qos_class(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi)
    if cls == "BestEffort":
        return ("No requests and no limits. The scheduler assumes this pod needs nothing, "
                "so it can land anywhere, and it is first in line to be evicted when the "
                "node runs short of memory.")
    if cls == "Guaranteed":
        return ("Both limits are set and requests match them. The pod is charged for exactly "
                "what it reserved, and it is evicted last under node memory pressure.")
    reasons = []
    if lim_cpu_m == 0:
        reasons.append("no CPU limit")
    if lim_mem_mi == 0:
        reasons.append("no memory limit")
    if lim_cpu_m > 0 and 0 < req_cpu_m != lim_cpu_m:
        reasons.append("CPU request differs from its limit")
    if lim_mem_mi > 0 and 0 < req_mem_mi != lim_mem_mi:
        reasons.append("memory request differs from its limit")
    why = ", ".join(reasons) if reasons else "requests and limits do not line up"
    return (f"Burstable because {why}. The pod may use more than it reserved when the node "
            "has room, but that headroom is not guaranteed and can vanish at any moment.")


def qos_view(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi):
    cls = qos_class(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi)
    fmt = lambda v, unit: ("not set" if v == 0 else f"{v}{unit}")  # noqa: E731
    return {
        "kind": "stack",
        "panels": [
            {
                "kind": "scalars",
                "items": [
                    {"label": "QoS class", "value": cls},
                    {"label": "CPU request", "value": fmt(req_cpu_m, "m")},
                    {"label": "CPU limit", "value": fmt(lim_cpu_m, "m")},
                    {"label": "Memory request", "value": fmt(req_mem_mi, "Mi")},
                    {"label": "Memory limit", "value": fmt(lim_mem_mi, "Mi")},
                ],
            },
            {"kind": "text", "text": qos_explanation(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi)},
        ],
    }


# ------------------------------------------------------------- enforcement


def enforcement_outcome(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi, use_cpu_m, use_mem_mi):
    """What the kubelet and kernel actually do when a container exceeds its limits.

    CPU is a compressible resource and is throttled. Memory is incompressible,
    so exceeding its limit gets the container killed.
    """
    if lim_cpu_m > 0 and use_cpu_m > lim_cpu_m:
        cpu_action, cpu_note = "throttled", (
            f"The cgroup CPU quota caps it at {lim_cpu_m}m. The process keeps running, "
            f"just slower, denied {use_cpu_m - lim_cpu_m}m of the CPU it asked for."
        )
    else:
        cpu_action, cpu_note = "none", "CPU use is within the limit, so nothing intervenes."

    if lim_mem_mi > 0 and use_mem_mi > lim_mem_mi:
        mem_action, mem_note = "oomkilled", (
            f"Memory cannot be throttled. Crossing {lim_mem_mi}Mi makes the kernel OOM killer "
            "terminate the container; the kubelet restarts it and the restart count climbs."
        )
    else:
        mem_action, mem_note = "none", "Memory use is within the limit."

    return {
        "cpu_action": cpu_action,
        "mem_action": mem_action,
        "cpu_note": cpu_note,
        "mem_note": mem_note,
        "container_restarts": mem_action == "oomkilled",
    }


def enforcement_view(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi, use_cpu_m, use_mem_mi):
    out = enforcement_outcome(req_cpu_m, lim_cpu_m, req_mem_mi, lim_mem_mi, use_cpu_m, use_mem_mi)
    return {
        "kind": "stack",
        "panels": [
            {
                "kind": "bars",
                "labels": ["CPU limit", "CPU used", "Mem limit", "Mem used"],
                "values": [lim_cpu_m, use_cpu_m, lim_mem_mi, use_mem_mi],
                "highlight": [1, 3],
                "x_label": "millicores (CPU) and MiB (memory)",
                "y_label": "quantity",
                "value_format": "d",
                "caption": "Both resources are over their limit, but only one of them kills you.",
            },
            {"kind": "text", "text": f"CPU: {out['cpu_note']}"},
            {"kind": "text", "text": f"Memory: {out['mem_note']}"},
        ],
    }


# --------------------------------------------------------------- scheduling


def _blank_state():
    return {
        "nodes": [dict(n, used_cpu_m=0, used_mem_mi=0, pods=[]) for n in NODES],
        "queue": [dict(p) for p in POD_QUEUE],
        "cursor": 0,
        "pending": [],
        "last": None,
        "done": False,
    }


def schedule_init():
    return _blank_state()


def _utilisation(node):
    return (node["used_cpu_m"] / node["cpu_m"] + node["used_mem_mi"] / node["mem_mi"]) / 2.0


def schedule_step(state):
    """Place the next pod on the feasible node with the lowest utilisation."""
    s = {
        "nodes": [dict(n, pods=list(n["pods"])) for n in state["nodes"]],
        "queue": [dict(p) for p in state["queue"]],
        "cursor": state["cursor"],
        "pending": list(state["pending"]),
        "last": None,
        "done": state["done"],
    }
    if s["done"] or s["cursor"] >= len(s["queue"]):
        s["done"] = True
        return s

    pod = s["queue"][s["cursor"]]
    feasible = [n for n in s["nodes"]
                if n["used_cpu_m"] + pod["cpu_m"] <= n["cpu_m"]
                and n["used_mem_mi"] + pod["mem_mi"] <= n["mem_mi"]]

    if feasible:
        # Stable tie-break: node order, matching the deterministic walk we describe.
        chosen = min(feasible, key=lambda n: (_utilisation(n), s["nodes"].index(n)))
        chosen["used_cpu_m"] += pod["cpu_m"]
        chosen["used_mem_mi"] += pod["mem_mi"]
        chosen["pods"] = chosen["pods"] + [pod["name"]]
        s["last"] = {"pod": pod["name"], "node": chosen["name"], "scheduled": True}
    else:
        s["pending"].append(pod["name"])
        free_cpu = sum(n["cpu_m"] - n["used_cpu_m"] for n in s["nodes"])
        s["last"] = {
            "pod": pod["name"], "node": None, "scheduled": False,
            "free_cpu_m": free_cpu, "needed_cpu_m": pod["cpu_m"],
        }

    s["cursor"] += 1
    if s["cursor"] >= len(s["queue"]):
        s["done"] = True
    return s


def schedule_view(state):
    nodes = state["nodes"]
    cells = []
    for n in nodes:
        cpu_pct = n["used_cpu_m"] / n["cpu_m"]
        mem_pct = n["used_mem_mi"] / n["mem_mi"]
        cells.append([
            {"v": n["used_cpu_m"], "label": f"{n['used_cpu_m']}m / {n['cpu_m']}m",
             "state": "active" if cpu_pct > 0.8 else ("filled" if cpu_pct > 0 else "empty")},
            {"v": n["cpu_m"] - n["used_cpu_m"], "label": f"{n['cpu_m'] - n['used_cpu_m']}m",
             "state": "filled" if cpu_pct > 0 else "empty"},
            {"v": n["used_mem_mi"], "label": f"{n['used_mem_mi']}Mi / {n['mem_mi']}Mi",
             "state": "active" if mem_pct > 0.8 else ("filled" if mem_pct > 0 else "empty")},
            {"v": len(n["pods"]), "label": ", ".join(n["pods"]) or "none",
             "state": "filled" if n["pods"] else "empty"},
        ])

    last = state.get("last")
    if last is None:
        caption = f"{len(state['queue'])} pods waiting. Each is placed on the feasible node with the lowest utilisation."
    elif last["scheduled"]:
        caption = f"{last['pod']} scheduled onto {last['node']}."
    else:
        caption = (
            f"{last['pod']} is Pending. It needs {last['needed_cpu_m']}m of CPU and the cluster has "
            f"{last['free_cpu_m']}m free, but no single node has enough. Requests are satisfied "
            "per-node, never pooled across the cluster."
        )

    panels = [{
        "kind": "grid",
        "col_labels": ["CPU used", "CPU free", "Memory used", "Pods"],
        "row_labels": [n["name"] for n in nodes],
        "cells": cells,
        "caption": caption,
    }]

    if state["pending"]:
        panels.append({"kind": "text",
                       "text": f"Pending: {', '.join(state['pending'])}. The scheduler will retry, "
                               "but nothing changes until capacity appears or the pod shrinks."})
    return {"kind": "stack", "panels": panels}


def fit_view(pod_cpu_m, pod_mem_mi):
    """Where would a single pod land on an already-loaded cluster?"""
    loaded = [
        {"name": "node-a", "cpu_m": 4000, "mem_mi": 8192, "used_cpu_m": 3500, "used_mem_mi": 4096},
        {"name": "node-b", "cpu_m": 4000, "mem_mi": 8192, "used_cpu_m": 1500, "used_mem_mi": 2048},
        {"name": "node-c", "cpu_m": 4000, "mem_mi": 8192, "used_cpu_m": 1000, "used_mem_mi": 3072},
    ]
    feasible = [n for n in loaded
                if n["used_cpu_m"] + pod_cpu_m <= n["cpu_m"]
                and n["used_mem_mi"] + pod_mem_mi <= n["mem_mi"]]
    chosen = min(feasible, key=lambda n: (_utilisation(n), loaded.index(n))) if feasible else None

    free_cpu = sum(n["cpu_m"] - n["used_cpu_m"] for n in loaded)
    free_mem = sum(n["mem_mi"] - n["used_mem_mi"] for n in loaded)
    if chosen:
        caption = (f"Fits on {chosen['name']}, which has "
                   f"{chosen['cpu_m'] - chosen['used_cpu_m']}m CPU and "
                   f"{chosen['mem_mi'] - chosen['used_mem_mi']}Mi free.")
    else:
        caption = (f"Pending. Cluster-wide there is {free_cpu}m CPU and {free_mem}Mi memory free, "
                   "but a pod must fit entirely on one node.")

    return {
        "kind": "stack",
        "panels": [
            {
                "kind": "bars",
                "labels": [f"{n['name']} free CPU" for n in loaded] + ["pod requests"],
                "values": [n["cpu_m"] - n["used_cpu_m"] for n in loaded] + [pod_cpu_m],
                "highlight": [3],
                "x_label": "node",
                "y_label": "millicores",
                "value_format": "d",
                "caption": caption,
            },
            {
                "kind": "bars",
                "labels": [f"{n['name']} free mem" for n in loaded] + ["pod requests"],
                "values": [n["mem_mi"] - n["used_mem_mi"] for n in loaded] + [pod_mem_mi],
                "highlight": [3],
                "x_label": "node",
                "y_label": "MiB",
                "value_format": "d",
            },
        ],
    }


# ------------------------------------------------------------- YAML grading

BUDGET_CPU_M = 2000
BUDGET_MEM_MI = 4096


def grade_manifest(submission):
    """Grade a learner-written Pod manifest.

    Returns {passed, message, details:[{label, ok, note}]}. The learner's text
    is data here, never executed.
    """
    details = []

    def add(label, ok, note=""):
        details.append({"label": label, "ok": bool(ok), "note": note})
        return ok

    try:
        doc = yaml.safe_load(submission)
    except yaml.YAMLError as e:
        return {"passed": False, "message": "That is not valid YAML.",
                "details": [{"label": "Parses as valid YAML", "ok": False,
                             "note": str(e).split("\n")[0][:200]}]}

    if not isinstance(doc, dict):
        return {"passed": False, "message": "Expected a YAML mapping describing a Pod.",
                "details": [{"label": "Parses as valid YAML", "ok": True, "note": ""},
                            {"label": "Top level is a mapping", "ok": False,
                             "note": f"got {type(doc).__name__}"}]}

    add("Parses as valid YAML", True)
    add("kind is Pod", doc.get("kind") == "Pod", f"found kind: {doc.get('kind')!r}")

    containers = (doc.get("spec") or {}).get("containers")
    if not isinstance(containers, list) or not containers:
        add("spec.containers is a non-empty list", False, "no containers found")
        return {"passed": False, "message": "The manifest has no containers.", "details": details}
    add("spec.containers is a non-empty list", True)

    total_cpu = 0
    total_mem = 0
    all_guaranteed = True
    parse_ok = True

    for i, c in enumerate(containers):
        name = (c or {}).get("name", f"containers[{i}]")
        res = (c or {}).get("resources") or {}
        req = res.get("requests") or {}
        lim = res.get("limits") or {}
        try:
            rc, lc = parse_cpu(req.get("cpu", 0)), parse_cpu(lim.get("cpu", 0))
            rm, lm = parse_mem(req.get("memory", 0)), parse_mem(lim.get("memory", 0))
        except (ValueError, TypeError) as e:
            add(f"{name}: quantities are parseable", False, str(e)[:160])
            parse_ok = False
            all_guaranteed = False
            continue

        # An omitted request defaults to the limit, so the budget must count
        # whichever one is present. Otherwise a manifest with no requests
        # would look like it consumes nothing.
        total_cpu += rc or lc
        total_mem += rm or lm

        # Limits are what Guaranteed requires. A requests block is optional:
        # each omitted request defaults to its limit, which qos_class already
        # accounts for, so a limits-only manifest is Guaranteed and must not be
        # marked wrong here.
        if not lim:
            add(f"{name}: cpu and memory limits set", False,
                "no limits block, and a container without limits can never be Guaranteed")
            all_guaranteed = False
            continue

        add(f"{name}: cpu and memory limits set", True)
        cls = qos_class(rc, lc, rm, lm)
        if cls != "Guaranteed":
            all_guaranteed = False
        add(f"{name}: requests equal limits", cls == "Guaranteed",
            f"this container is {cls}" if cls != "Guaranteed" else "")

    add("Pod QoS class is Guaranteed", all_guaranteed and parse_ok,
        "every container needs cpu and memory limits, and any request it does "
        "state must equal its limit")
    fits = total_cpu <= BUDGET_CPU_M and total_mem <= BUDGET_MEM_MI
    add(f"Fits the {BUDGET_CPU_M}m CPU / {BUDGET_MEM_MI}Mi budget", fits,
        f"this pod requests {total_cpu}m CPU and {total_mem}Mi memory")

    passed = all(d["ok"] for d in details)
    if passed:
        message = (f"Guaranteed, requesting {total_cpu}m CPU and {total_mem}Mi memory. "
                   "This pod is evicted last under node memory pressure.")
    else:
        message = "Not there yet. See the checks below."
    return {"passed": passed, "message": message, "details": details}
