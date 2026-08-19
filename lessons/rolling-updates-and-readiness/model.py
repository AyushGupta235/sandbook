"""Model functions for "Rolling Updates and Readiness".

Hand-written. Every function is pure: JSON in, JSON out, no I/O, no global
mutation. The verifier runs these in CPython and the browser runs them in
Pyodide, through the same bootstrap.
"""

import math


# ------------------------------------------------------------ rollout order


def rollout_order():
    """The order a Deployment rollout actually proceeds in, and why.

    The constraints are the point. A rollout is not a fixed script; it is a
    set of dependencies, and several orderings satisfy them. Returning the
    constraints rather than one blessed sequence lets the widget accept any
    ordering that would really work, instead of the one that happens to be
    written here.
    """
    order = [
        "edit",
        "new-rs",
        "surge-pod",
        "probe-pass",
        "endpoint-add",
        "old-terminate",
        "endpoint-remove",
    ]
    constraints = [
        # Nothing happens until the spec changes.
        ["edit", "new-rs"],
        # The ReplicaSet is what creates the pod.
        ["new-rs", "surge-pod"],
        # A pod cannot pass a readiness probe before it exists.
        ["surge-pod", "probe-pass"],
        # This is the rule people get wrong: readiness gates endpoints.
        ["probe-pass", "endpoint-add"],
        # And this is the one that matters for availability. The old pod is
        # only safe to kill once the new one is actually serving.
        ["endpoint-add", "old-terminate"],
        # The old pod also keeps serving until the new one does. Note what is
        # deliberately *not* constrained: nothing orders the termination
        # against the endpoint removal, because in practice they happen
        # together. A pod entering Terminating is pulled from endpoints at
        # about the same moment it receives SIGTERM, so either order is a fair
        # description and the widget accepts both.
        ["endpoint-add", "endpoint-remove"],
    ]
    return {"order": order, "constraints": constraints}


# ------------------------------------------------------- surge availability


def rollout_capacity(replicas, max_surge, max_unavailable):
    """Pod counts a Deployment is allowed to reach during a rolling update.

    max_unavailable is subtracted from the desired count, not from what is
    currently running, which is why a rollout with maxUnavailable=0 never dips
    below full capacity however slow it is.
    """
    replicas = int(replicas)
    max_surge = int(max_surge)
    max_unavailable = int(max_unavailable)
    if replicas < 1:
        raise ValueError("replicas must be at least 1")
    if max_surge < 0 or max_unavailable < 0:
        raise ValueError("surge and unavailable cannot be negative")

    ceiling = replicas + max_surge
    floor = max(0, replicas - max_unavailable)
    return {
        "replicas": replicas,
        "max_total": ceiling,
        "min_available": floor,
        "extra_capacity_needed": max_surge,
        # A rollout where both knobs are zero cannot make progress: it may not
        # add a pod and it may not remove one.
        "can_progress": (max_surge > 0 or max_unavailable > 0),
    }


def rollout_capacity_view(replicas, max_surge, max_unavailable):
    c = rollout_capacity(replicas, max_surge, max_unavailable)
    bars = {
        "kind": "bars",
        "labels": ["Guaranteed serving", "Desired", "Peak pods"],
        "values": [c["min_available"], c["replicas"], c["max_total"]],
        "y_label": "pods",
        "value_format": "d",
        "highlight": [0],
        "caption": (
            f"With {c['replicas']} replicas, surge {max_surge} and unavailable "
            f"{max_unavailable}: at no point are fewer than {c['min_available']} pods "
            f"serving, and the cluster must have room for {c['max_total']} at once."
        ),
    }
    if not c["can_progress"]:
        note = ("Both knobs are zero, so the rollout is stuck: it is not allowed to "
                "create a pod before deleting one, or to delete one before creating one.")
    elif c["min_available"] == c["replicas"]:
        note = ("maxUnavailable is 0, so capacity never dips. You pay for it in headroom: "
                f"the cluster needs space for {max_surge} extra pod(s) during the rollout.")
    else:
        shortfall = c["replicas"] - c["min_available"]
        note = (f"Capacity can dip by {shortfall} pod(s) mid-rollout. That is fine if "
                f"{c['min_available']} pods can carry peak traffic, and an outage if not.")
    return {"kind": "stack", "panels": [bars, {"kind": "text", "text": note}]}


def rollout_min_available(replicas, max_surge, max_unavailable):
    return rollout_capacity(replicas, max_surge, max_unavailable)["min_available"]


def rollout_peak(replicas, max_surge, max_unavailable):
    return rollout_capacity(replicas, max_surge, max_unavailable)["max_total"]


# --------------------------------------------------------- the stuck rollout


def stuck_rollout_facts():
    """A rollout that will never finish, and the number that proves it.

    10 replicas, maxUnavailable 0, maxSurge 1, and a readiness probe pointed at
    a port nothing listens on. The new pod starts, never becomes ready, is
    never added to endpoints, and the old pod is therefore never terminated.
    """
    replicas = 10
    c = rollout_capacity(replicas, max_surge=1, max_unavailable=0)
    return {
        "replicas": replicas,
        "updated_pods": 1,
        "ready_new_pods": 0,
        "available": replicas,
        "peak_pods": c["max_total"],
        "progress_percent": 0.0,
        "serving_old_version": replicas,
    }


def stuck_rollout_view():
    f = stuck_rollout_facts()
    return {
        "kind": "stack",
        "panels": [
            {
                "kind": "scalars",
                "items": [
                    {"label": "Pods serving traffic", "value": f["available"]},
                    {"label": "New pods created", "value": f["updated_pods"]},
                    {"label": "New pods passing readiness", "value": f["ready_new_pods"]},
                    {"label": "Rollout progress", "value": f["progress_percent"]},
                ],
                "caption": (
                    "Every user request is still served, and by the old version. This is "
                    "why a broken rollout can sit unnoticed for hours: nothing is down."
                ),
            },
            {
                "kind": "text",
                "text": (
                    "The surge pod exists and is running. It is not ready, so it is not in "
                    "the Service endpoints, so it receives nothing. With maxUnavailable at 0 "
                    "the controller may not terminate an old pod until a new one is "
                    "available, and none ever will be. The rollout is not slow, it is "
                    "finished making progress."
                ),
            },
        ],
    }


# ------------------------------------------------------- surge headroom hunt


def surge_headroom_goal(replicas, max_surge, max_unavailable, node_capacity):
    """Is this rollout both safe and possible on a cluster this size?

    Three things have to hold at once, and the settings that satisfy any two of
    them usually break the third. That tension is the exercise.
    """
    c = rollout_capacity(replicas, max_surge, max_unavailable)
    node_capacity = int(node_capacity)

    fits = c["max_total"] <= node_capacity
    no_dip = c["min_available"] >= replicas
    progresses = c["can_progress"]

    if not progresses:
        return {"met": False,
                "message": "This rollout cannot start.",
                "detail": ("With surge 0 and unavailable 0 the controller may neither add a "
                           "pod nor remove one, so it never takes a first step.")}
    if not fits:
        return {"met": False,
                "message": f"Needs room for {c['max_total']} pods, cluster holds {node_capacity}.",
                "detail": ("The surge pod has nowhere to be scheduled, so it sits Pending and "
                           "the rollout waits on it.")}
    if not no_dip:
        return {"met": False,
                "message": f"Capacity dips to {c['min_available']} of {replicas} during the rollout.",
                "detail": ("Allowed, but this lesson's requirement is that every replica keeps "
                           "serving throughout.")}
    return {"met": True,
            "message": f"Never below {replicas} serving, peaks at {c['max_total']}, fits in {node_capacity}.",
            "detail": "Surge without unavailability, and the cluster has room for the extra pod."}


def surge_headroom_view(replicas, max_surge, max_unavailable, node_capacity):
    c = rollout_capacity(replicas, max_surge, max_unavailable)
    return {
        "kind": "bars",
        "labels": ["Guaranteed serving", "Desired", "Peak pods", "Cluster capacity"],
        "values": [c["min_available"], int(replicas), c["max_total"], int(node_capacity)],
        "y_label": "pods",
        "value_format": "d",
        "highlight": [2, 3],
        "caption": ("Peak pods must fit under cluster capacity, and guaranteed serving must "
                    "reach the desired count. Both bars have to land in the right place."),
    }


# -------------------------------------------------------- how long it takes


def rollout_batches(replicas, max_surge, max_unavailable):
    """How many sequential waves a rollout takes, and its total wall time.

    Each wave replaces as many pods as the two budgets allow at once. This is
    the arithmetic people skip when they set maxSurge to 1 on a 50-replica
    deployment and then wonder why the deploy takes an hour.
    """
    c = rollout_capacity(replicas, max_surge, max_unavailable)
    if not c["can_progress"]:
        raise ValueError("a rollout with no surge and no unavailability never proceeds")
    per_wave = int(max_surge) + int(max_unavailable)
    waves = math.ceil(int(replicas) / per_wave)
    return {"per_wave": per_wave, "waves": waves}


def rollout_duration_s(replicas, max_surge, max_unavailable, readiness_delay_s):
    b = rollout_batches(replicas, max_surge, max_unavailable)
    return b["waves"] * int(readiness_delay_s)


def rollout_duration_view(replicas, max_surge, max_unavailable, readiness_delay_s):
    b = rollout_batches(replicas, max_surge, max_unavailable)
    total = rollout_duration_s(replicas, max_surge, max_unavailable, readiness_delay_s)
    return {
        "kind": "scalars",
        "items": [
            {"label": "Pods replaced per wave", "value": b["per_wave"]},
            {"label": "Waves needed", "value": b["waves"]},
            {"label": "Seconds per wave", "value": int(readiness_delay_s)},
            {"label": "Total seconds", "value": total},
        ],
        "value_format": "d",
        "caption": (f"{int(replicas)} replicas at {b['per_wave']} per wave is {b['waves']} waves, "
                    f"and each wave waits {int(readiness_delay_s)}s for readiness: {total}s in total. "
                    "The readiness delay is multiplied by the wave count, which is why a "
                    "conservative surge setting is expensive on a large deployment."),
    }
