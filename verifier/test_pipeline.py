"""Regression tests for the generation pipeline.

These run against recorded model replies rather than a live model, so they need
no credentials, cost nothing, and fail only when the harness changes. That is
the point: a failure here means the pipeline regressed, not that a model had an
off day.

What is being pinned:

  * a clean module ships unchanged
  * a module the verifier rejects is sent back and ships once repaired
  * a module that cannot be repaired is dropped, not shipped broken
  * a lesson where nothing survives writes nothing at all
  * whatever does ship passes the same verifier a hand-written lesson faces

Run:  python3 verifier/test_pipeline.py   (or ./sandbook selftest)
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "verifier"))

import pipeline  # noqa: E402
from llm import ScriptedModel  # noqa: E402
from verify import verify_lesson  # noqa: E402

FIXTURES = ROOT / "harness" / "fixtures"


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def build_with(fixture: pathlib.Path, topic: str):
    model = ScriptedModel.from_file(fixture)
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        report = pipeline.build(model, topic, output_root=out, on_event=lambda *_: None)
        findings = None
        if report.path is not None:
            findings = verify_lesson(report.slug, lessons_dir=out)
        written = sorted(p.name for p in out.iterdir()) if out.exists() else []
        document, source = None, ""
        if report.path is not None:
            # Read inside the context manager; the directory is gone after it.
            document = json.loads((report.path / "lesson.json").read_text())
            source = (report.path / "model.py").read_text()
        return report, findings, written, document, source


# --------------------------------------------------------------------- cases


def case_token_bucket() -> str:
    report, findings, _, document, source = build_with(FIXTURES / "token-bucket.json",
                                                       "token bucket rate limiting")

    check(report.shipped == ["bucket-shape", "predict-idle"],
          f"expected two modules to ship, got {report.shipped}")
    check([mid for mid, _ in report.dropped] == ["refill-walk"],
          f"expected refill-walk to be dropped, got {[m for m, _ in report.dropped]}")
    check(report.repairs >= 1, "expected at least one repair round")

    # The repaired module must actually be the repaired version: the broken one
    # had two true predicates, so a shipped lesson proves the fix took effect.
    check(findings.errors == 0,
          f"the shipped lesson does not verify: {findings.items[:2]}")

    # Fail-closed: the dropped module must be absent from the document, not
    # present-but-broken.
    ids = [m["id"] for m in document["modules"]]
    check("refill-walk" not in ids, "a dropped module leaked into the lesson")
    check(len(ids) == 2, f"expected 2 modules in the document, got {ids}")

    # A module reusing an earlier module's helper must survive verification.
    check("tb_burst_headroom" in source and "tb_available" in source,
          "model.py is missing functions the modules declared")

    return (f"shipped {len(report.shipped)}, dropped {len(report.dropped)}, "
            f"{report.repairs} repair round(s), lesson verifies clean")


def case_nothing_survives() -> str:
    """Every module is unrepairable, so the build must write nothing."""
    broken_module = {
        "prose": "x",
        "widget": {"type": "step-sim", "title": "t", "max_steps": 3,
                   "init": {"fn": "z_init", "args": {}},
                   "step": {"fn": "z_step", "args": {"state": {"state": True}}},
                   "view": {"fn": "z_view", "args": {"state": {"state": True}}}},
        "functions": [
            {"name": "z_init", "source": 'def z_init():\n    return {"t": 0, "done": False}'},
            {"name": "z_step", "source": 'def z_step(state):\n    s = dict(state)\n    s["t"] += 1\n    return s'},
            {"name": "z_view", "source": 'def z_view(state):\n    return {"kind": "scalars", "items": [{"label": "t", "value": state["t"]}]}'},
        ],
    }
    curriculum = {
        "slug": "doomed", "title": "Doomed", "subtitle": "s", "packages": [],
        "objectives": ["o"], "misconceptions": [{"claim": "c", "reality": "r"}],
        "modules": [{"id": "only", "title": "Only", "widget_type": "step-sim",
                     "intent": "i", "teaching_note": "n"}],
    }
    calls = [{"stage": "curriculum", "system": "", "prompt": "", "reply": json.dumps(curriculum)}]
    calls.append({"stage": "module", "system": "", "prompt": "", "reply": json.dumps(broken_module)})
    for _ in range(pipeline.MAX_REPAIR_ROUNDS):
        calls.append({"stage": "repair", "system": "", "prompt": "", "reply": json.dumps(broken_module)})

    with tempfile.TemporaryDirectory() as tmp:
        fixture = pathlib.Path(tmp) / "doomed.json"
        fixture.write_text(json.dumps({"calls": calls}))
        report, findings, written, _, _ = build_with(fixture, "a doomed topic")

    check(not report.ok, "a build with no surviving module reported success")
    check(report.path is None, f"nothing should have been written, got {report.path}")
    check(written == [], f"the output directory should be empty, found {written}")
    check(len(report.dropped) == 1, f"expected one dropped module, got {report.dropped}")
    return "no module survived, nothing written, failure reported"


def case_malformed_reply_is_retried() -> str:
    """A reply in the wrong shape earns a second ask, not an immediate drop.

    This is the failure that cost a real module: a code-cell whose widget names
    no function came back with an empty functions list, and the build threw the
    module away rather than asking again. The repair loop cannot cover this,
    because it repairs a parsed module and there was nothing to parse.
    """
    good = {
        "prose": "p",
        "widget": {"type": "step-sim", "title": "t", "max_steps": 2,
                   "init": {"fn": "r_init", "args": {}},
                   "step": {"fn": "r_step", "args": {"state": {"state": True}}},
                   "view": {"fn": "r_view", "args": {"state": {"state": True}}}},
        "functions": [
            {"name": "r_init", "source": 'def r_init():\n    return {"t": 0, "done": False}'},
            {"name": "r_step", "source": 'def r_step(state):\n    s = dict(state)\n'
                                         '    s["t"] += 1\n    s["done"] = s["t"] >= 2\n    return s'},
            {"name": "r_view", "source": 'def r_view(state):\n    return {"kind": "scalars",\n'
                                         '        "items": [{"label": "t", "value": state["t"]}]}'},
        ],
    }
    empty_functions = dict(good, functions=[])
    curriculum = {
        "slug": "retried", "title": "Retried", "subtitle": "s", "packages": [],
        "objectives": ["o"], "misconceptions": [{"claim": "c", "reality": "r"}],
        "modules": [{"id": "only", "title": "Only", "widget_type": "step-sim",
                     "intent": "i", "teaching_note": "n"}],
    }
    calls = [
        {"stage": "curriculum", "system": "", "prompt": "", "reply": json.dumps(curriculum)},
        {"stage": "module", "system": "", "prompt": "", "reply": json.dumps(empty_functions)},
        {"stage": "module", "system": "", "prompt": "", "reply": json.dumps(good)},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        fixture = pathlib.Path(tmp) / "retried.json"
        fixture.write_text(json.dumps({"calls": calls}))
        report, findings, _, _, _ = build_with(fixture, "a topic worth a second ask")

    check(report.ok, f"the rebuilt module should have shipped: {report.dropped}")
    check(report.shipped == ["only"], f"expected the module to ship, got {report.shipped}")
    check(report.dropped == [], f"nothing should have been dropped, got {report.dropped}")
    check(findings is not None and findings.errors == 0, "the shipped lesson does not verify")
    return "a malformed first reply was rebuilt rather than dropped"


CASES = [
    ("token-bucket: repair and fail-closed drop", case_token_bucket),
    ("nothing survives: writes nothing", case_nothing_survives),
    ("malformed reply: rebuilt, not dropped", case_malformed_reply_is_retried),
]


def main() -> int:
    print("pipeline regression (recorded replies, no model calls)")
    failures = 0
    for name, case in CASES:
        try:
            detail = case()
        except Failure as e:
            failures += 1
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001 - surface unexpected breakage verbatim
            failures += 1
            print(f"  ✗ {name}\n      unexpected {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}\n      {detail}")

    print()
    if failures:
        print(f"{failures}/{len(CASES)} pipeline cases failed")
        return 1
    print(f"all {len(CASES)} pipeline cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
