"""Tests for the kernels themselves.

A kernel is the thing generated code is measured against, so it does not get to
be the untested part. Two layers here:

  * a contract check every kernel must satisfy, so a new kernel cannot ship
    half-declared or with probes that raise
  * property tests per kernel, asserting the behaviour that makes it right
    rather than re-deriving the same arithmetic a second time, which would only
    prove the formula was copied consistently

Run:  python3 kernels/test_kernels.py   (or ./sandbook selftest)
"""

from __future__ import annotations

import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import kernels  # noqa: E402


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol + tol * abs(b)


# ------------------------------------------------------------------- contract


def test_contract() -> str:
    names = kernels.available()
    check(bool(names), "no kernels found at all")
    for name in names:
        k = kernels.load(name)
        for field in ("NAME", "SUMMARY", "PROBES"):
            check(hasattr(k, field), f"{name}: kernel declares no {field}")
        check(k.NAME == name, f"{name}: NAME is {k.NAME!r}, which is not the file name")
        check(callable(getattr(k, "reference", None)), f"{name}: no reference() to call")
        check(len(k.PROBES) >= 3,
              f"{name}: {len(k.PROBES)} probe(s); too few to separate a right "
              "implementation from a plausible wrong one")
        for i, probe in enumerate(k.PROBES):
            check(isinstance(probe, dict), f"{name}: probe {i} is not a kwargs dict")
            try:
                out = k.reference(**probe)
            except Exception as e:  # noqa: BLE001
                raise Failure(f"{name}: probe {i} raised {type(e).__name__}: {e}") from e
            try:
                json.dumps(out, allow_nan=False)
            except (TypeError, ValueError) as e:
                raise Failure(f"{name}: probe {i} returned something that is not "
                              f"JSON with finite numbers: {e}") from e
    return f"{len(names)} kernel(s) declared correctly, every probe runs"


# ----------------------------------------------------------------- properties


def test_softmax() -> str:
    ref = kernels.load("softmax").reference

    for probe in kernels.load("softmax").PROBES:
        p = ref(**probe)
        check(close(sum(p), 1.0), f"probabilities sum to {sum(p)} for {probe}")
        check(all(0.0 <= x <= 1.0 for x in p), f"probability outside [0, 1] for {probe}")
        check(all(math.isfinite(x) for x in p), f"non-finite probability for {probe}")

    logits = [3.0, 2.6, 2.0, 0.5, -0.5]
    # Order is preserved: a larger logit never gets a smaller probability.
    p = ref(logits, 1.0)
    check(p == sorted(p, reverse=True), "softmax reordered a descending logit vector")

    # Lower temperature concentrates mass on the top token.
    sharp, flat = ref(logits, 0.1), ref(logits, 8.0)
    check(max(sharp) > max(flat),
          f"lowering temperature did not sharpen: {max(sharp)} vs {max(flat)}")

    # Uniform logits stay uniform at any temperature.
    for t in (0.05, 1.0, 20.0):
        u = ref([0.0, 0.0, 0.0, 0.0], t)
        check(all(close(x, 0.25) for x in u), f"uniform logits gave {u} at T={t}")

    # Shifting every logit by a constant leaves the distribution unchanged.
    shifted = ref([x + 17.0 for x in logits], 1.0)
    check(all(close(a, b) for a, b in zip(p, shifted)),
          "adding a constant to every logit changed the distribution")

    # The overflow case, where the naive formula raises instead of answering.
    big = ref([1000.0, 1001.0], 1.0)
    check(all(math.isfinite(x) for x in big), f"large logits produced {big}")
    check(close(big[1], 1.0 / (1.0 + math.exp(-1.0))),
          f"large-logit case gave {big}, which is not the right distribution")

    return "sums to 1, order preserved, temperature sharpens, shift invariant, stable"


def test_retry_backoff() -> str:
    ref = kernels.load("retry_backoff").reference

    # Attempts, not retries: 5 attempts means 4 waits.
    r = ref(1.0, 2.0, 100.0, 5)
    check(r["delays_s"] == [1.0, 2.0, 4.0, 8.0], f"plain doubling gave {r['delays_s']}")
    check(close(r["total_wait_s"], 15.0), f"total was {r['total_wait_s']}")
    check(r["capped_from"] == -1, "reported a cap that was never reached")

    # One attempt is no retry at all.
    check(ref(1.0, 2.0, 100.0, 1)["delays_s"] == [], "a single attempt still waited")

    # The cap flattens the curve and is reported from the right index.
    capped = ref(1.0, 2.0, 10.0, 8)
    check(capped["delays_s"] == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0],
          f"capped schedule was {capped['delays_s']}")
    check(capped["capped_from"] == 4,
          f"cap first bound at index {capped['capped_from']}, expected 4")

    # Monotone and bounded, whatever the settings.
    for probe in kernels.load("retry_backoff").PROBES:
        delays = ref(**probe)["delays_s"]
        cap = probe["maximum_interval_s"]
        check(all(d <= cap + 1e-12 for d in delays), f"a delay exceeded the cap for {probe}")
        check(all(b >= a - 1e-12 for a, b in zip(delays, delays[1:])),
              f"delays decreased for {probe}")

    # Coefficient 1 is constant backoff.
    flat = ref(2.0, 1.0, 60.0, 4)["delays_s"]
    check(all(close(d, 2.0) for d in flat), f"coefficient 1 gave {flat}")

    # Unlimited attempts are bounded by the horizon instead.
    check(len(ref(0.5, 3.0, 60.0, 0, horizon=7)["delays_s"]) == 7,
          "unlimited attempts ignored the horizon")

    return "attempts vs retries, cap flattens and is located, monotone, constant case"


def test_kv_cache_bytes() -> str:
    ref = kernels.load("kv_cache_bytes").reference
    base = {"batch": 1, "seq_len": 4096, "n_layers": 32, "n_kv_heads": 8,
            "head_dim": 128, "dtype_bytes": 2}

    # Nothing is cached before the first token.
    check(ref(**dict(base, seq_len=0))["bytes"] == 0, "an empty context held bytes")

    # Linear in every dimension, one at a time.
    for field in ("batch", "seq_len", "n_layers", "n_kv_heads", "head_dim", "dtype_bytes"):
        doubled = ref(**dict(base, **{field: base[field] * 2}))["bytes"]
        check(doubled == ref(**base)["bytes"] * 2,
              f"doubling {field} did not double the cache")

    # Grouped-query attention against multi-head: 8 KV heads instead of 32 is
    # exactly a quarter. This is the claim a wrong formula gets wrong.
    gqa = ref(**base)["bytes"]
    mha = ref(**dict(base, n_kv_heads=32))["bytes"]
    check(mha == gqa * 4, f"MHA/GQA ratio was {mha / gqa}, expected 4")

    # Per-token cost does not depend on batch or context length.
    per_token = ref(**base)["bytes_per_token"]
    check(ref(**dict(base, batch=64, seq_len=131072))["bytes_per_token"] == per_token,
          "per-token cost changed with batch or context length")
    check(ref(**base)["bytes"] == per_token * base["batch"] * base["seq_len"],
          "total does not equal per-token cost times tokens held")

    return "zero at empty, linear in each factor, GQA is exactly 1/4 of MHA"


def test_dag_order() -> str:
    ref = kernels.load("dag_order").reference

    for probe in kernels.load("dag_order").PROBES:
        r = ref(**probe)
        if r["has_cycle"]:
            check(r["order"] == [], f"a cyclic graph still returned an order for {probe}")
            check(bool(r["in_cycle"]), "a cycle was reported with no nodes named")
            continue
        # The defining property: every edge is respected.
        pos = {n: i for i, n in enumerate(r["order"])}
        check(sorted(r["order"]) == sorted(probe["nodes"]),
              f"the order is not a permutation of the nodes for {probe}")
        for a, b in probe["edges"]:
            check(pos[a] < pos[b], f"edge {a} -> {b} was violated in {r['order']}")
        # Reversing a valid apply order is a valid destroy order.
        rpos = {n: i for i, n in enumerate(reversed(r["order"]))}
        for a, b in probe["edges"]:
            check(rpos[b] < rpos[a], f"reversed order broke {a} -> {b}")

    # Levels are the longest chain, not merely a valid layering.
    diamond = ref(nodes=["vpc", "sg", "subnet", "instance"],
                  edges=[["vpc", "sg"], ["vpc", "subnet"],
                         ["sg", "instance"], ["subnet", "instance"]])
    check(diamond["levels"] == {"vpc": 0, "sg": 1, "subnet": 1, "instance": 2},
          f"diamond levels wrong: {diamond['levels']}")
    check(diamond["width"] == 2, f"diamond width should be 2, got {diamond['width']}")

    check(ref(nodes=["a", "b", "c"], edges=[])["width"] == 3,
          "three independent nodes can all be worked at once")

    # A self-edge is the smallest cycle and must be caught.
    loop = ref(nodes=["a", "b"], edges=[["a", "a"], ["a", "b"]])
    check(loop["has_cycle"] and "a" in loop["in_cycle"],
          f"a self-dependency is a cycle: {loop}")

    try:
        ref(nodes=["a"], edges=[["a", "ghost"]])
        raise Failure("an edge to an unknown node should raise")
    except ValueError:
        pass
    return "edges respected, reverse is a valid destroy order, levels and cycles correct"


def test_scheduler_fit() -> str:
    ref = kernels.load("scheduler_fit").reference
    nodes = [{"name": "node-a", "cpu_m": 4000, "mem_mi": 8192},
             {"name": "node-b", "cpu_m": 4000, "mem_mi": 8192},
             {"name": "node-c", "cpu_m": 4000, "mem_mi": 8192}]

    # The fragmentation case: 3000m free on every node, 6000m free in total,
    # and a 2000m pod that cannot run because capacity does not pool.
    r = ref(nodes=nodes, pods=[{"name": "a", "cpu_m": 3000, "mem_mi": 2048},
                               {"name": "b", "cpu_m": 3000, "mem_mi": 2048},
                               {"name": "c", "cpu_m": 3000, "mem_mi": 2048},
                               {"name": "batch", "cpu_m": 2000, "mem_mi": 1024}])
    check(r["pending"] == ["batch"], f"expected only batch to be pending, got {r['pending']}")
    check(r["free_cpu_m"] == 3000, f"cluster should have 3000m free, got {r['free_cpu_m']}")
    check(r["stranded"] is True, "a pod pending while the cluster has room is stranded")
    check(r["unschedulable"] == [], "batch would fit an empty node, so it is not unschedulable")

    # Exact fit is a fit; one unit more is not.
    tiny = [{"name": "only", "cpu_m": 1000, "mem_mi": 1024}]
    check(ref(nodes=tiny, pods=[{"name": "exact", "cpu_m": 1000, "mem_mi": 1024}])["pending"] == [],
          "a pod that exactly fills a node must be placed")
    check(ref(nodes=tiny, pods=[{"name": "over", "cpu_m": 1001, "mem_mi": 1024}])["pending"] == ["over"],
          "one millicore over must not be placed")

    # Memory can bind while cpu is nearly untouched.
    mem_bound = ref(nodes=nodes, pods=[{"name": f"c{i}", "cpu_m": 100, "mem_mi": 8000}
                                       for i in range(4)])
    check(len(mem_bound["pending"]) == 1, f"memory should bind here: {mem_bound}")
    check(mem_bound["free_cpu_m"] > 11000, "cpu should be almost entirely free")

    # A pod bigger than any node never becomes schedulable.
    huge = ref(nodes=nodes, pods=[{"name": "huge", "cpu_m": 9000, "mem_mi": 1024}])
    check(huge["unschedulable"] == ["huge"], f"expected huge to be unschedulable: {huge}")
    check(huge["stranded"] is False,
          "a pod too large for any node is not stranded by fragmentation")

    check(ref(nodes=nodes, pods=[])["placements"] == {}, "no pods means no placements")
    return "fragmentation, exact fits, memory binding, and permanently unschedulable pods"


TESTS = [
    ("every kernel satisfies the contract", test_contract),
    ("dag_order", test_dag_order),
    ("scheduler_fit", test_scheduler_fit),
    ("softmax", test_softmax),
    ("retry_backoff", test_retry_backoff),
    ("kv_cache_bytes", test_kv_cache_bytes),
]


def main() -> int:
    print("kernel tests")
    failed = 0
    for name, fn in TESTS:
        try:
            print(f"  ✓ {name}\n      {fn()}")
        except Failure as e:
            failed += 1
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {name}\n      unexpected {type(e).__name__}: {e}")
    print(f"\n{'all kernel tests passed' if not failed else f'{failed} kernel test(s) failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
