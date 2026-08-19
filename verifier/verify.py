"""Verifies that a lesson is structurally sound, executable, and honestly scored.

A generated lesson is treated as untrusted until checked, because a confident
wrong explanation leaves a learner worse off than no lesson at all. This runs
before a lesson ships, and a single ERROR blocks it.

Layers:
  1. structure   : shape of lesson.json, known widget types, sane param specs
  2. references  : every function/param a widget names actually exists
  3. execution   : every view renders, at every corner of its parameter space
  4. contract    : the pedagogy rules that make answers checkable:
                     * predict-reveal: exactly one option predicate holds
                     * code-cell: the solution passes AND the starter fails
                     * step-sim: terminates within max_steps

Usage:  python3 verifier/verify.py [lesson-slug ...]
"""

from __future__ import annotations

import datetime
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kernels  # noqa: E402  (needs ROOT on the path first)

LESSONS = ROOT / "lessons"
RUNNER = ROOT / "verifier" / "runner.py"
TIMEOUT_S = 120

WIDGET_TYPES = {"param-playground", "predict-reveal", "step-sim", "code-cell",
                "order-build", "bug-hunt", "param-hunt", "calc-widget", "diff-apply"}
VIEW_KINDS = {"bars", "lines", "grid", "scalars", "text", "stack"}

# House style, enforced rather than requested. The prompts ask for this too, but
# a prompt is a request and a check is a guarantee, and asking alone did not
# work: the first generated lesson came back with 38 of them.
BANNED_CHARS = {"—": "em-dash", "–": "en-dash"}

# How long a version-pinned lesson is assumed to still describe its tool. Infra
# tooling moves fast enough that a year-old lesson on a Kubernetes API or a
# Temporal SDK is a plausible source of confidently outdated instruction, which
# is the same harm as being wrong, arriving more slowly.
STALE_AFTER_DAYS = 365


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def error(self, where: str, msg: str) -> None:
        self.items.append(("ERROR", where, msg))

    def warn(self, where: str, msg: str) -> None:
        self.items.append(("WARN", where, msg))

    @property
    def errors(self) -> int:
        return sum(1 for s, _, _ in self.items if s == "ERROR")

    @property
    def warnings(self) -> int:
        return sum(1 for s, _, _ in self.items if s == "WARN")


# ----------------------------------------------------------------- house style


def _excerpt(text: str, char: str, span: int = 34) -> str:
    """The offending text with a little context, on one line."""
    i = text.find(char)
    lo, hi = max(0, i - span), min(len(text), i + span + 1)
    return ("..." if lo else "") + " ".join(text[lo:hi].split()) + ("..." if hi < len(text) else "")


def check_prose(node, where: str, f: Findings, path: str = "") -> None:
    """Flag banned punctuation anywhere a learner can read it.

    Walks both the static lesson.json and the values that come back from
    executed views, because a caption assembled inside an f-string reaches the
    learner exactly like one written in the config, and only one of those two
    is visible to a static pass.
    """
    if isinstance(node, str):
        for char, name in BANNED_CHARS.items():
            if char in node:
                f.error(where, f"{name} in {path or 'text'}: {_excerpt(node, char)!r} "
                               "(use a comma, a colon, a semicolon, or two sentences)")
                return
    elif isinstance(node, dict):
        for key, value in node.items():
            check_prose(value, where, f, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            check_prose(value, where, f, f"{path}[{i}]")


def check_staleness(lesson: dict, where: str, f: Findings) -> None:
    """A lesson pinned to a tool version has a shelf life.

    Nothing here can tell whether the tool has actually changed. What it can do
    is refuse to let a lesson quietly keep claiming accuracy for a version it
    was written against a long time ago, which is how a lesson goes from correct
    to confidently outdated without anyone editing it.
    """
    for si, source in enumerate(lesson.get("sources") or []):
        if not isinstance(source, dict) or not source.get("url") or not source.get("title"):
            f.error(where, f"sources[{si}] needs a title and a url; a citation nobody "
                           "can follow is not a citation")

    targets = lesson.get("targets")
    if not targets:
        return
    if not lesson.get("sources"):
        f.warn(where, f"claims accuracy for {targets!r} but cites no source, "
                      "so the claim rests on nothing a reader can check")
    written = lesson.get("generated_on")
    if not written:
        f.warn(where, f"claims accuracy for {targets!r} but records no date, "
                      "so nothing can tell whether that is still true")
        return
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(written)).days
    except ValueError:
        f.error(where, f"generated_on is {written!r}, which is not a date")
        return
    if age > STALE_AFTER_DAYS:
        f.warn(where, f"targets {targets!r} and was written {age} days ago; "
                      "check it against current docs before trusting it")


# --------------------------------------------------------------- view checking


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_view(view, where: str, f: Findings) -> None:
    """A view is the only thing generated code is allowed to draw with, so its
    shape is checked strictly. A labels/values length mismatch silently
    mislabels a chart, which is exactly the failure mode we cannot ship."""
    if not isinstance(view, dict):
        f.error(where, f"expected a view object, got {type(view).__name__}")
        return
    kind = view.get("kind")
    if kind not in VIEW_KINDS:
        f.error(where, f"unknown view kind {kind!r}; expected one of {sorted(VIEW_KINDS)}")
        return

    if kind == "bars":
        values = view.get("values")
        labels = view.get("labels")
        if not isinstance(values, list) or not values:
            f.error(where, "bars view needs a non-empty 'values' list")
            return
        bad = [i for i, v in enumerate(values) if not is_num(v)]
        if bad:
            f.error(where, f"bars 'values' has non-finite entries at indices {bad[:5]}")
        if not isinstance(labels, list):
            f.error(where, "bars view needs a 'labels' list")
        elif len(labels) != len(values):
            f.error(where, f"bars has {len(labels)} labels but {len(values)} values, "
                           "the chart would mislabel its bars")
        for i in view.get("highlight", []) or []:
            if not isinstance(i, int) or not (0 <= i < len(values)):
                f.error(where, f"bars 'highlight' index {i} is out of range")

    elif kind == "lines":
        xs = view.get("x")
        series = view.get("series")
        if not isinstance(xs, list) or not xs:
            f.error(where, "lines view needs a non-empty 'x' list")
            return
        if not isinstance(series, list) or not series:
            f.error(where, "lines view needs a non-empty 'series' list")
            return
        for si, s in enumerate(series):
            vals = s.get("values") if isinstance(s, dict) else None
            if not isinstance(vals, list):
                f.error(where, f"series[{si}] needs a 'values' list")
                continue
            if len(vals) != len(xs):
                f.error(where, f"series[{si}] has {len(vals)} points but x has {len(xs)}, "
                               "the curve would be plotted against the wrong x values")
            bad = [i for i, v in enumerate(vals) if not is_num(v)]
            if bad:
                f.error(where, f"series[{si}] has non-finite values at indices {bad[:5]}")
            if not s.get("label"):
                f.warn(where, f"series[{si}] has no label")

    elif kind == "grid":
        cells = view.get("cells")
        if not isinstance(cells, list) or not cells:
            f.error(where, "grid view needs a non-empty 'cells' list")
            return
        cols = view.get("col_labels")
        rows = view.get("row_labels")
        if isinstance(rows, list) and len(rows) != len(cells):
            f.error(where, f"grid has {len(rows)} row labels but {len(cells)} rows")
        for ri, row in enumerate(cells):
            if not isinstance(row, list):
                f.error(where, f"grid row {ri} is not a list")
            elif isinstance(cols, list) and len(row) != len(cols):
                f.error(where, f"grid row {ri} has {len(row)} cells but {len(cols)} column labels")

    elif kind == "scalars":
        items = view.get("items")
        if not isinstance(items, list) or not items:
            f.error(where, "scalars view needs a non-empty 'items' list")
            return
        for i, it in enumerate(items):
            if not isinstance(it, dict) or "value" not in it:
                f.error(where, f"scalars items[{i}] needs a 'value'")
            elif not it.get("label"):
                f.warn(where, f"scalars items[{i}] has no label")

    elif kind == "text":
        if not isinstance(view.get("text"), str) or not view["text"].strip():
            f.error(where, "text view needs non-empty 'text'")

    elif kind == "stack":
        panels = view.get("panels")
        if not isinstance(panels, list) or not panels:
            f.error(where, "stack view needs a non-empty 'panels' list")
            return
        for i, p in enumerate(panels):
            check_view(p, f"{where}.panels[{i}]", f)


# ------------------------------------------------------- structure & planning


def param_corners(params: list[dict]) -> list[dict]:
    """Default settings, plus each parameter pushed to its extremes with the
    rest left at default. Linear in the number of params rather than
    exponential, but still exercises every boundary a learner can reach."""
    base = {p["id"]: p.get("default") for p in params}
    combos = [dict(base)]
    for p in params:
        alts = []
        if p.get("kind") == "choice":
            alts = [o["value"] for o in p.get("options", [])]
        else:
            alts = [p.get("min"), p.get("max")]
        for a in alts:
            if a is None:
                continue
            combo = dict(base)
            combo[p["id"]] = a
            if combo not in combos:
                combos.append(combo)
    return combos


def resolve_args(spec: dict, scope: dict, where: str, declared: set, f: Findings):
    """Mirror of runtime/bind.js. Returns resolved args, flagging bad bindings."""
    out = {}
    for key, binding in (spec or {}).items():
        if isinstance(binding, dict) and "const" in binding:
            out[key] = binding["const"]
        elif isinstance(binding, dict) and "param" in binding:
            name = binding["param"]
            if name not in declared:
                f.error(where, f"argument {key!r} binds to undeclared param {name!r}")
                out[key] = None
            else:
                out[key] = scope.get(name)
        elif isinstance(binding, dict) and "state" in binding:
            out[key] = {"$state": True}
        else:
            out[key] = binding
    return out


def check_params(params, where, f: Findings) -> set:
    declared = set()
    for i, p in enumerate(params):
        pid = p.get("id")
        if not pid:
            f.error(where, f"params[{i}] has no id")
            continue
        if pid in declared:
            f.error(where, f"duplicate param id {pid!r}")
        declared.add(pid)
        if p.get("kind") == "choice":
            opts = p.get("options") or []
            if not opts:
                f.error(where, f"choice param {pid!r} has no options")
            values = [o.get("value") for o in opts]
            if p.get("default") not in values:
                f.error(where, f"choice param {pid!r} default {p.get('default')!r} is not among its options")
        else:
            lo, hi, d = p.get("min"), p.get("max"), p.get("default")
            if not all(is_num(x) for x in (lo, hi, d)):
                f.error(where, f"range param {pid!r} needs numeric min/max/default")
                continue
            if lo >= hi:
                f.error(where, f"range param {pid!r} has min >= max")
            if not (lo <= d <= hi):
                f.error(where, f"range param {pid!r} default {d} is outside [{lo}, {hi}]")
            if p.get("step") is not None and (not is_num(p["step"]) or p["step"] <= 0):
                f.error(where, f"range param {pid!r} has a non-positive step")
    return declared


# ------------------------------------------------------------------ main pass


def verify_lesson(slug: str, lessons_dir: pathlib.Path | None = None) -> Findings:
    f = Findings()
    d = (lessons_dir or LESSONS) / slug
    lesson_path = d / "lesson.json"
    if not lesson_path.exists():
        f.error(slug, f"missing {lesson_path}")
        return f

    try:
        lesson = json.loads(lesson_path.read_text())
    except json.JSONDecodeError as e:
        f.error(slug, f"lesson.json is not valid JSON: {e}")
        return f

    for field in ("slug", "title", "modules"):
        if not lesson.get(field):
            f.error(slug, f"lesson.json is missing required field {field!r}")
    if lesson.get("slug") and lesson["slug"] != slug:
        f.error(slug, f"lesson.json slug {lesson['slug']!r} does not match its directory name")

    model_path = d / lesson.get("model", "model.py")
    if not model_path.exists():
        f.error(slug, f"model file {model_path.name} not found")
        return f
    source = model_path.read_text()

    check_prose({k: v for k, v in lesson.items() if k != "modules"}, slug, f)
    check_staleness(lesson, slug, f)

    # ---- plan the execution ops -------------------------------------------
    ops: list[dict] = []
    plan: list[dict] = []  # parallel metadata describing what each op proves

    for mi, module in enumerate(lesson.get("modules", [])):
        mid = module.get("id") or f"modules[{mi}]"
        check_prose(module, mid, f)
        widget = module.get("widget")
        if not module.get("title"):
            f.warn(mid, "module has no title")
        if not widget:
            f.warn(mid, "module has no widget, it is prose only")
            continue
        wtype = widget.get("type")
        where = f"{mid}/{wtype}"
        if wtype not in WIDGET_TYPES:
            f.error(mid, f"unknown widget type {wtype!r}; the runtime cannot render it")
            continue

        if wtype == "param-playground":
            params = widget.get("params") or []
            declared = check_params(params, where, f)
            view = widget.get("view") or {}
            if not view.get("fn"):
                f.error(where, "param-playground needs view.fn")
                continue
            for combo in param_corners(params):
                ops.append({"op": "call", "fn": view["fn"],
                            "args": resolve_args(view.get("args"), combo, where, declared, f)})
                plan.append({"kind": "view", "where": f"{where} view@{combo}"})
            for ri, r in enumerate(widget.get("readouts") or []):
                if not r.get("fn"):
                    f.error(where, f"readouts[{ri}] needs an fn")
                    continue
                base = {p["id"]: p.get("default") for p in params}
                ops.append({"op": "call", "fn": r["fn"],
                            "args": resolve_args(r.get("args"), base, where, declared, f)})
                plan.append({"kind": "scalar", "where": f"{where} readouts[{ri}]"})

        elif wtype == "predict-reveal":
            options = widget.get("options") or []
            check = widget.get("check") or {}
            if len(options) < 2:
                f.error(where, "predict-reveal needs at least two options")
            if not check.get("fn"):
                f.error(where, "predict-reveal needs check.fn; the answer must be derived, not asserted")
                continue
            missing = [o.get("id") for o in options if not o.get("predicate")]
            if missing:
                f.error(where, f"options {missing} have no predicate; their correctness cannot be derived")
                continue
            for key in ("correct", "answer", "correct_id"):
                if key in widget:
                    f.error(where, f"predict-reveal must not assert an answer via {key!r}; "
                                   "correctness is derived by executing the predicates")
            ops.append({"op": "check", "fn": check["fn"],
                        "args": resolve_args(check.get("args"), {}, where, set(), f),
                        "predicates": [o["predicate"] for o in options]})
            plan.append({"kind": "predicates", "where": where,
                         "options": [o.get("id") for o in options]})
            if widget.get("view", {}).get("fn"):
                v = widget["view"]
                ops.append({"op": "call", "fn": v["fn"],
                            "args": resolve_args(v.get("args"), {}, where, set(), f)})
                plan.append({"kind": "view", "where": f"{where} reveal view"})

        elif wtype == "calc-widget":
            answer = widget.get("answer") or {}
            if not answer.get("fn"):
                f.error(where, "calc-widget needs answer.fn; the expected value is computed, "
                               "not written into the config")
                continue
            for key in ("value", "expected", "correct"):
                if key in widget:
                    f.error(where, f"calc-widget must not assert the answer via {key!r}")
            tol = widget.get("tolerance", 1e-6)
            if not is_num(tol) or tol < 0:
                f.error(where, f"tolerance must be a non-negative number, got {tol!r}")
            ops.append({"op": "call", "fn": answer["fn"],
                        "args": resolve_args(answer.get("args"), {}, where, set(), f)})
            plan.append({"kind": "calc-answer", "where": where})
            if (widget.get("working") or {}).get("fn"):
                w = widget["working"]
                ops.append({"op": "call", "fn": w["fn"],
                            "args": resolve_args(w.get("args"), {}, where, set(), f)})
                plan.append({"kind": "view", "where": f"{where} working"})

        elif wtype == "param-hunt":
            params = widget.get("params") or []
            declared = check_params(params, where, f)
            goal = widget.get("goal") or {}
            if not goal.get("fn"):
                f.error(where, "param-hunt needs goal.fn; whether the goal is met is decided "
                               "by running it, not by the config")
                continue
            defaults = {p["id"]: p.get("default") for p in params}
            ops.append({"op": "call", "fn": goal["fn"],
                        "args": resolve_args(goal.get("args"), defaults, where, declared, f)})
            plan.append({"kind": "hunt-default", "where": where, "widget": where})
            # Somewhere in the space the goal has to be reachable, or the
            # learner is being asked to find something that is not there.
            for combo in param_corners(params):
                ops.append({"op": "call", "fn": goal["fn"],
                            "args": resolve_args(goal.get("args"), combo, where, declared, f)})
                plan.append({"kind": "hunt-corner", "where": f"{where} @{combo}",
                             "widget": where})
            if (widget.get("view") or {}).get("fn"):
                v = widget["view"]
                for combo in param_corners(params):
                    ops.append({"op": "call", "fn": v["fn"],
                                "args": resolve_args(v.get("args"), combo, where, declared, f)})
                    plan.append({"kind": "view", "where": f"{where} view@{combo}"})

        elif wtype == "diff-apply":
            code = widget.get("code")
            tests = widget.get("tests")
            candidates = widget.get("candidates") or []
            if not isinstance(code, str) or not isinstance(tests, str) or not tests.strip():
                f.error(where, "diff-apply needs 'code' and hidden 'tests'")
                continue
            if len(candidates) < 2:
                f.error(where, "diff-apply needs at least two candidate changes")
                continue
            for key in ("correct", "answer"):
                if key in widget:
                    f.error(where, f"diff-apply must not assert an answer via {key!r}")
            if any(not isinstance(c.get("code"), str) or not c["code"].strip()
                   for c in candidates):
                f.error(where, "every candidate needs a 'code': the whole listing as it "
                               "would be after the change")
                continue
            ops.append({"op": "exec", "code": code, "tests": tests})
            plan.append({"kind": "bug-original", "where": where})
            for c in candidates:
                ops.append({"op": "exec", "code": c["code"], "tests": tests})
                plan.append({"kind": "bug-candidate", "where": where, "widget": where,
                             "id": c.get("id", "?"), "line": 0, "total": len(candidates)})

        elif wtype == "bug-hunt":
            code = widget.get("code")
            tests = widget.get("tests")
            candidates = widget.get("candidates") or []
            if not isinstance(code, str) or not code.strip():
                f.error(where, "bug-hunt needs a 'code' listing")
                continue
            if not isinstance(tests, str) or not tests.strip():
                f.error(where, "bug-hunt needs hidden 'tests'; without them nothing "
                               "decides which line is wrong")
                continue
            if len(candidates) < 2:
                f.error(where, "bug-hunt needs at least two candidate lines, or there is "
                               "nothing to choose between")
                continue
            for key in ("correct", "answer", "buggy_line"):
                if key in widget:
                    f.error(where, f"bug-hunt must not assert an answer via {key!r}; the "
                                   "right line is the one whose patch makes the tests pass")
            n_lines = len(code.split("\n"))
            bad_lines = [c.get("line") for c in candidates
                         if not isinstance(c.get("line"), int)
                         or not (1 <= c["line"] <= n_lines)]
            if bad_lines:
                f.error(where, f"candidate line(s) {bad_lines} are outside the "
                               f"{n_lines}-line listing")
                continue
            if len({c["line"] for c in candidates}) != len(candidates):
                f.error(where, "two candidates point at the same line")
                continue
            if any(not isinstance(c.get("patch"), str) for c in candidates):
                f.error(where, "every candidate needs a 'patch': the replacement for its line")
                continue

            # The listing as shipped must be broken, and exactly one candidate
            # patch must repair it. Both facts are settled by running the tests.
            ops.append({"op": "exec", "code": code, "tests": tests})
            plan.append({"kind": "bug-original", "where": where})
            for c in candidates:
                lines = code.split("\n")
                lines[c["line"] - 1] = c["patch"]
                ops.append({"op": "exec", "code": "\n".join(lines), "tests": tests})
                plan.append({"kind": "bug-candidate", "where": where,
                             "widget": where, "id": c.get("id", str(c["line"])),
                             "line": c["line"], "total": len(candidates)})

        elif wtype == "order-build":
            items = widget.get("items") or []
            order = widget.get("order") or {}
            ids = [it.get("id") for it in items if isinstance(it, dict)]
            if len(items) < 3:
                f.error(where, "order-build needs at least three steps to be worth ordering")
            if len(set(ids)) != len(ids) or not all(ids):
                f.error(where, f"item ids must all be present and unique, got {ids}")
            if not order.get("fn"):
                f.error(where, "order-build needs order.fn; the right order is derived, "
                               "not written into the config")
                continue
            for key in ("correct_order", "answer", "solution"):
                if key in widget:
                    f.error(where, f"order-build must not assert an answer via {key!r}")
            ops.append({"op": "call", "fn": order["fn"],
                        "args": resolve_args(order.get("args"), {}, where, set(), f)})
            plan.append({"kind": "ordering", "where": where, "ids": ids,
                         "listed": [it.get("id") for it in items if isinstance(it, dict)]})

        elif wtype == "step-sim":
            init, step, view = widget.get("init"), widget.get("step"), widget.get("view")
            if not (init and step and view and init.get("fn") and step.get("fn") and view.get("fn")):
                f.error(where, "step-sim needs init.fn, step.fn and view.fn")
                continue
            max_steps = widget.get("max_steps", 32)
            init_idx = len(ops)
            ops.append({"op": "call", "fn": init["fn"],
                        "args": resolve_args(init.get("args"), {}, where, set(), f)})
            plan.append({"kind": "sim-init", "where": f"{where} init"})

            state_ref = init_idx
            for s in range(max_steps):
                ops.append({"op": "call", "fn": view["fn"],
                            "args": {k: ({"$ref": state_ref} if isinstance(v, dict) and "state" in v else v)
                                     for k, v in (view.get("args") or {}).items()}})
                plan.append({"kind": "view", "where": f"{where} view@step{s}"})
                ops.append({"op": "call", "fn": step["fn"],
                            "args": {k: ({"$ref": state_ref} if isinstance(v, dict) and "state" in v else v)
                                     for k, v in (step.get("args") or {}).items()}})
                plan.append({"kind": "sim-step", "where": f"{where} step{s + 1}", "index": s + 1,
                             "max_steps": max_steps, "widget": where})
                state_ref = len(ops) - 1

        elif wtype == "code-cell":
            graded = bool((widget.get("grade") or {}).get("fn"))
            for field in ("task", "starter", "solution"):
                if not widget.get(field):
                    f.error(where, f"code-cell needs a non-empty {field!r}")
            if not graded and not widget.get("tests"):
                f.error(where, "code-cell needs either 'tests' (python mode) or 'grade.fn' (graded mode)")
            if widget.get("tests") and graded:
                f.error(where, "code-cell declares both 'tests' and 'grade.fn'; pick one mode")
            if not (widget.get("solution") and widget.get("starter")):
                continue
            if f.errors:
                continue

            # Both modes must prove the same two things: the reference solution
            # passes, and the starter does not.
            if graded:
                fn = widget["grade"]["fn"]
                ops.append({"op": "call", "fn": fn, "args": {"submission": widget["solution"]}})
                plan.append({"kind": "graded-solution", "where": where})
                ops.append({"op": "call", "fn": fn, "args": {"submission": widget["starter"]}})
                plan.append({"kind": "graded-starter", "where": where})
            else:
                ops.append({"op": "exec", "code": widget["solution"], "tests": widget["tests"]})
                plan.append({"kind": "solution", "where": where})
                ops.append({"op": "exec", "code": widget["starter"], "tests": widget["tests"]})
                plan.append({"kind": "starter", "where": where})

    # ---- claims measured against a trusted kernel --------------------------
    for ci, claim in enumerate(lesson.get("implements") or []):
        where = f"implements[{ci}]"
        fn_name, kernel_name = claim.get("fn"), claim.get("kernel")
        if not fn_name or not kernel_name:
            f.error(where, "an implements entry needs both 'fn' and 'kernel'")
            continue
        try:
            kernel = kernels.load(kernel_name)
        except KeyError:
            f.error(where, f"{fn_name}() claims to implement {kernel_name!r}, "
                           f"which is not a kernel. Available:\n{kernels.describe()}")
            continue
        except Exception as e:  # noqa: BLE001 - a broken kernel must not pass silently
            f.error(where, f"kernel {kernel_name!r} failed to load: {type(e).__name__}: {e}")
            continue
        for pi, probe in enumerate(kernel.PROBES):
            ops.append({"op": "call", "fn": fn_name, "args": probe})
            plan.append({"kind": "kernel", "where": f"{where} {fn_name}() vs {kernel_name}",
                         "kernel": kernel_name, "probe": probe, "index": pi})

    if f.errors:
        return f  # planning already failed; executing would only add noise

    # ---- execute -----------------------------------------------------------
    try:
        proc = subprocess.run(
            [sys.executable, str(RUNNER)],
            input=json.dumps({"source": source, "ops": ops,
                              "packages": lesson.get("packages") or []}),
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        f.error(slug, f"model execution exceeded {TIMEOUT_S}s, likely an infinite loop in model.py")
        return f

    if proc.returncode != 0:
        f.error(slug, f"runner crashed (exit {proc.returncode}): {proc.stderr.strip()[:600]}")
        return f
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        f.error(slug, f"runner produced unreadable output: {proc.stdout[:300]!r}")
        return f
    if not out.get("ok"):
        f.error(slug, f"model.py failed to load: {out.get('error')}\n{out.get('trace', '')}")
        return f

    names = set(out.get("names", []))
    for op in ops:
        if op["op"] in ("call", "check") and op["fn"] not in names:
            f.error(slug, f"widget references {op['fn']}() but model.py defines no such function")
    if f.errors:
        return f

    # ---- interpret ---------------------------------------------------------
    sim_done: dict[str, int] = {}
    fixes: dict[str, list[tuple[str, int, bool]]] = {}
    reachable: set[str] = set()

    for meta, res in zip(plan, out.get("results", [])):
        where = meta["where"]
        kind = meta["kind"]

        if kind == "kernel":
            if not res.get("ok"):
                f.error(where, f"probe {meta['index']} {meta['probe']} raised: {res.get('error')}")
                continue
            kernel = kernels.load(meta["kernel"])
            try:
                expected = kernel.reference(**meta["probe"])
            except Exception as e:  # noqa: BLE001
                f.error(where, f"the kernel itself raised on probe {meta['index']}: "
                               f"{type(e).__name__}: {e}")
                continue
            tol = getattr(kernel, "TOLERANCE", kernels.DEFAULT_TOLERANCE)
            found = kernels.disagreement(res.get("result"), expected, tol)
            if found:
                f.error(where, f"disagrees with the kernel on probe {meta['index']} "
                               f"{meta['probe']}. {found}")
            continue

        if kind in ("view", "scalar", "sim-init", "sim-step"):
            if not res.get("ok"):
                f.error(where, f"call failed: {res.get('error')}")
                continue

        if kind == "view":
            check_view(res.get("result"), where, f)
            check_prose(res.get("result"), where, f)

        elif kind == "scalar":
            v = res.get("result")
            if not (is_num(v) or isinstance(v, str)):
                f.error(where, f"readout returned {type(v).__name__}; expected a number or string")

        elif kind == "sim-init":
            if not isinstance(res.get("result"), dict):
                f.error(where, "step-sim init must return a state object (dict)")

        elif kind == "sim-step":
            state = res.get("result")
            if isinstance(state, dict) and state.get("done") and meta["widget"] not in sim_done:
                sim_done[meta["widget"]] = meta["index"]

        elif kind == "calc-answer":
            v = res.get("result")
            if not is_num(v):
                f.error(where, f"the answer function returned {v!r}, which is not a finite "
                               "number, so nothing can be marked right or wrong")

        elif kind in ("hunt-default", "hunt-corner"):
            result = res.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("met"), bool):
                f.error(where, "goal.fn must return an object with a boolean 'met'")
                continue
            if kind == "hunt-default":
                if result["met"]:
                    f.error(meta["widget"], "the goal is already met at the default settings, "
                                            "so the learner has nothing to find")
            elif result["met"]:
                reachable.add(meta["widget"])

        elif kind == "bug-original":
            if res.get("ok"):
                f.error(where, "the code as shipped already passes its own tests, so there "
                               "is no bug to find")

        elif kind == "bug-candidate":
            fixes.setdefault(meta["widget"], []).append(
                (meta["id"], meta["line"], bool(res.get("ok"))))

        elif kind == "ordering":
            result = res.get("result")
            if not isinstance(result, dict):
                f.error(where, "order.fn must return an object with 'order' and 'constraints'")
                continue
            canonical = result.get("order")
            constraints = result.get("constraints")
            ids = meta["ids"]
            if not isinstance(canonical, list) or sorted(map(str, canonical)) != sorted(map(str, ids)):
                f.error(where, f"order.fn returned {canonical}, which is not an arrangement of "
                               f"the widget's items {ids}")
                continue
            if not isinstance(constraints, list) or not constraints:
                f.error(where, "order.fn declares no constraints, so every arrangement is "
                               "correct and the exercise asks nothing")
                continue

            position = {str(i): p for p, i in enumerate(canonical)}
            bad = [c for c in constraints
                   if not (isinstance(c, (list, tuple)) and len(c) == 2
                           and str(c[0]) in position and str(c[1]) in position)]
            if bad:
                f.error(where, f"constraints {bad[:3]} are not pairs of item ids")
                continue
            broken = [c for c in constraints if position[str(c[0])] > position[str(c[1])]]
            if broken:
                f.error(where, f"the order it calls correct breaks its own constraint(s) "
                               f"{broken[:3]}; the exercise cannot be solved as specified")
                continue
            # Solvable but not already solved: reading the steps top to bottom
            # must not already be a valid answer.
            listed = {str(i): p for p, i in enumerate(meta["listed"])}
            if not [c for c in constraints if listed[str(c[0])] > listed[str(c[1])]]:
                f.error(where, "the steps are already listed in a valid order, so the learner "
                               "solves this by pressing the buttons top to bottom")

        elif kind == "predicates":
            if not res.get("ok"):
                f.error(where, f"answer derivation failed: {res.get('error')}")
                continue
            flags = res.get("flags") or []
            true_ids = [oid for oid, flag in zip(meta["options"], flags) if flag]
            if len(true_ids) == 0:
                f.error(where, "no option predicate is true, so this question has no correct answer "
                               "and cannot be scored")
            elif len(true_ids) > 1:
                f.error(where, f"{len(true_ids)} option predicates are true ({true_ids}), "
                               "the question has multiple 'correct' answers")

        elif kind == "solution":
            if not res.get("ok"):
                f.error(where, "the provided solution does not pass its own checks: "
                               f"[{res.get('stage')}] {res.get('error')}")

        elif kind == "starter":
            if res.get("ok"):
                f.error(where, "the starter code already passes every check, so the exercise asks the "
                               "learner to fix something that is not broken")

        elif kind in ("graded-solution", "graded-starter"):
            if not res.get("ok"):
                f.error(where, f"grader raised an error: {res.get('error')}")
                continue
            result = res.get("result")
            if not isinstance(result, dict) or "passed" not in result:
                f.error(where, "grader must return an object with a 'passed' boolean")
                continue
            check_prose(result, where, f)
            if kind == "graded-solution" and not result["passed"]:
                f.error(where, "the provided solution is rejected by its own grader: "
                               f"{result.get('message', '(no message)')}")
            if kind == "graded-starter" and result["passed"]:
                f.error(where, "the starter submission already passes the grader, so the exercise asks "
                               "the learner to fix something that is not broken")
            if kind == "graded-starter" and not result["passed"]:
                if not result.get("message"):
                    f.warn(where, "grader gives no message on failure; the learner sees no feedback")
                details = result.get("details")
                # A rejected submission must say which check it failed. Otherwise
                # the learner is told "not there yet" with nothing to act on.
                if isinstance(details, list) and details and not any(
                        isinstance(d, dict) and d.get("ok") is False for d in details):
                    f.error(where, "grader rejects the starter but every individual check reports "
                                   "ok, so the learner is given no indication of what is wrong")

    for p in plan:
        if p["kind"] == "hunt-default" and p["widget"] not in reachable:
            f.error(p["widget"], "no corner of the parameter space meets the goal, so the "
                                 "learner is being asked to find something that is not there")

    for widget_where, results in fixes.items():
        working = [(cid, line) for cid, line, ok in results if ok]
        if not working:
            f.error(widget_where, "no candidate line fixes the tests, so the exercise has "
                                  "no right answer and the learner cannot win")
        elif len(working) > 1:
            f.error(widget_where, f"{len(working)} different lines each fix the tests "
                                  f"({working}); a learner who picks either is right and "
                                  "the module teaches nothing about which line was wrong")

    for p in plan:
        if p["kind"] == "sim-step" and p["index"] == p["max_steps"]:
            if p["widget"] not in sim_done:
                f.error(p["widget"], f"simulation never set done=true within max_steps="
                                     f"{p['max_steps']}; the learner would hit the cap mid-algorithm")

    return f


# ---------------------------------------------------------------------- entry


def main(argv: list[str]) -> int:
    slugs = argv[1:] or sorted(p.name for p in LESSONS.iterdir() if (p / "lesson.json").exists())
    if not slugs:
        print("no lessons found")
        return 1

    total_err = 0
    for slug in slugs:
        f = verify_lesson(slug)
        total_err += f.errors
        status = "FAIL" if f.errors else ("ok" if not f.warnings else "ok (warnings)")
        print(f"\n{'=' * 68}\n{slug}: {status}\n{'=' * 68}")
        if not f.items:
            print("  no findings")
        for severity, where, msg in f.items:
            marker = "✗" if severity == "ERROR" else "!"
            print(f"  {marker} [{severity}] {where}\n      {msg}")
        print(f"\n  {f.errors} error(s), {f.warnings} warning(s)")

    print()
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
