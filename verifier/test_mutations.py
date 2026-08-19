"""Mutation tests for the verifier.

A verifier that has never rejected anything is not evidence of correctness. This
suite deliberately breaks known-good lessons, one mutation per class of failure
the verifier claims to catch, and asserts each break is caught. If you add a
check to verify.py, add the mutation that proves it fires.

Run:  python3 verifier/test_mutations.py   (or ./sandbook selftest)
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verify import verify_lesson  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LESSONS = ROOT / "lessons"

PY_LESSON = "softmax-and-temperature"
YAML_LESSON = "kubernetes-requests-and-limits"
ORDER_LESSON = "rolling-updates-and-readiness"


def find_widget(lesson: dict, wtype: str, graded: bool | None = None) -> dict:
    for m in lesson["modules"]:
        w = m.get("widget", {})
        if w.get("type") != wtype:
            continue
        if graded is not None and bool(w.get("grade")) != graded:
            continue
        return w
    raise KeyError(f"{wtype} (graded={graded})")


# ------------------------------------------- mutations: python-mode lesson --


def mut_two_answers(lesson, model):
    """Loosen a predicate so two options are simultaneously true."""
    find_widget(lesson, "predict-reveal")["options"][0]["predicate"] = "max(result) > 0.5"
    return lesson, model


def mut_no_answer(lesson, model):
    """Make every predicate false, so the question becomes unscoreable."""
    for o in find_widget(lesson, "predict-reveal")["options"]:
        o["predicate"] = "max(result) > 0.999999"
    return lesson, model


def mut_asserted_answer(lesson, model):
    """Smuggle in a hardcoded answer key instead of deriving it."""
    find_widget(lesson, "predict-reveal")["correct"] = "near-one"
    return lesson, model


def mut_starter_already_correct(lesson, model):
    w = find_widget(lesson, "code-cell")
    w["starter"] = w["solution"]
    return lesson, model


def mut_broken_solution(lesson, model):
    w = find_widget(lesson, "code-cell")
    w["solution"] = w["solution"].replace("x - shift", "x")
    return lesson, model


def mut_label_mismatch(lesson, model):
    """Chart labelled with fewer categories than it plots."""
    return lesson, model + (
        "\n\n_orig_dist_view = dist_view\n"
        "def dist_view(preset, temperature):\n"
        "    v = _orig_dist_view(preset, temperature)\n"
        "    v['labels'] = v['labels'][:-1]\n"
        "    return v\n"
    )


def mut_nan_readout(lesson, model):
    return lesson, model + "\n\ndef readout_top_prob(preset, temperature):\n    return float('nan')\n"


def mut_missing_function(lesson, model):
    find_widget(lesson, "step-sim")["view"]["fn"] = "nucleus_view_typo"
    return lesson, model


def mut_bad_param_default(lesson, model):
    for p in find_widget(lesson, "param-playground")["params"]:
        if p.get("kind") != "choice":
            p["default"] = p["max"] + 10
    return lesson, model


def mut_nonterminating_sim(lesson, model):
    return lesson, model + (
        "\n\ndef nucleus_step(state):\n"
        "    s = dict(state)\n"
        "    s['cursor'] = min(s['cursor'] + 1, len(s['order']) - 1)\n"
        "    return s\n"
    )


# --------------------------------------------- mutations: graded-yaml lesson --


def mut_graded_starter_passes(lesson, model):
    """Ship a YAML exercise whose starter manifest is already correct."""
    w = find_widget(lesson, "code-cell", graded=True)
    w["starter"] = w["solution"]
    return lesson, model


def mut_graded_solution_fails(lesson, model):
    """Reference manifest that its own grader rejects."""
    w = find_widget(lesson, "code-cell", graded=True)
    w["solution"] = w["solution"].replace(
        "        limits:\n          cpu: 500m\n          memory: 512Mi\n", "")
    return lesson, model


def mut_grader_wrong_shape(lesson, model):
    """Grader that returns a bare bool instead of the documented object."""
    return lesson, model + "\n\ndef grade_manifest(submission):\n    return True\n"


def mut_grader_raises(lesson, model):
    return lesson, model + (
        "\n\ndef grade_manifest(submission):\n"
        "    raise RuntimeError('grader blew up')\n"
    )


def mut_both_modes(lesson, model):
    """Declare python-mode and graded mode at once, an ambiguous exercise."""
    find_widget(lesson, "code-cell", graded=True)["tests"] = "assert True"
    return lesson, model


def mut_grader_silent_rejection(lesson, model):
    """Grader rejects the starter while every individual check reports ok, so
    the learner is told 'no' with nothing to act on."""
    return lesson, model + (
        "\n\ndef grade_manifest(submission):\n"
        "    return {'passed': 'limits:' in submission, 'message': 'not yet',\n"
        "            'details': [{'label': 'looks fine', 'ok': True, 'note': ''}]}\n"
    )


def mut_kernel_disagreement(lesson, model):
    """A softmax that ignores temperature entirely.

    The kind of wrong that survives every other check in here: it returns the
    right shape, the probabilities still sum to 1, every view still renders.
    Only something that knows the right answer independently can catch it, and
    only at a temperature other than 1.
    """
    return lesson, model + (
        "\n\ndef softmax_probs(logits, temperature):\n"
        "    shift = max(logits)\n"
        "    exps = [math.exp(float(x) - shift) for x in logits]\n"
        "    total = sum(exps)\n"
        "    return [e / total for e in exps]\n"
    )


def mut_kernel_off_by_a_little(lesson, model):
    """Right formula, wrong direction: temperature multiplied, not divided."""
    return lesson, model + (
        "\n\ndef softmax_probs(logits, temperature):\n"
        "    scaled = [float(x) * max(float(temperature), 1e-6) for x in logits]\n"
        "    shift = max(scaled)\n"
        "    exps = [math.exp(x - shift) for x in scaled]\n"
        "    total = sum(exps)\n"
        "    return [e / total for e in exps]\n"
    )


def mut_unknown_kernel(lesson, model):
    """A claim against a kernel that does not exist must not pass quietly."""
    lesson["implements"] = [{"fn": "softmax_probs", "kernel": "softmax_but_better"}]
    return lesson, model


def mut_em_dash_in_prose(lesson, model):
    """House style: an em-dash written straight into the lesson config."""
    lesson["modules"][0]["prose"] += (
        "\n\nTemperature is not a confidence knob — it reshapes the distribution.")
    return lesson, model


def mut_en_dash_in_caption(lesson, model):
    """The harder case: a dash that only exists once a view has run.

    Nothing in lesson.json contains it, so a static scan over the config sees a
    clean lesson. It reaches the learner all the same.
    """
    return lesson, model + (
        "\n\n_orig_sweep_view = sweep_view\n"
        "def sweep_view(preset, t_min=0.05, t_max=3.0, steps=60):\n"
        "    v = _orig_sweep_view(preset, t_min, t_max, steps)\n"
        "    v['caption'] = 'entropy rises with temperature \\u2013 slowly at first'\n"
        "    return v\n"
    )


def mut_unfollowable_citation(lesson, model):
    """A source with a title and no link cannot be checked by anyone."""
    lesson["sources"] = [{"title": "the Kubernetes docs"}]
    return lesson, model


def mut_undateable_pin(lesson, model):
    """A version claim with a date nobody can read is worse than no date."""
    lesson["targets"] = "Kubernetes 1.29+"
    lesson["generated_on"] = "last spring"
    return lesson, model


def mut_sim_grid_mismatch(lesson, model):
    """Scheduler grid with more columns than it has headers."""
    return lesson, model + (
        "\n\n_orig_schedule_view = schedule_view\n"
        "def schedule_view(state):\n"
        "    v = _orig_schedule_view(state)\n"
        "    v['panels'][0]['col_labels'] = v['panels'][0]['col_labels'][:2]\n"
        "    return v\n"
    )


# ------------------------------------------------ mutations: order-build --


def mut_order_already_solved(lesson, model):
    """List the steps in an order that already satisfies every constraint.

    The exercise still renders and still scores; it just asks nothing, because
    clicking the steps top to bottom is a correct answer.
    """
    w = find_widget(lesson, "order-build")
    canonical = ["edit", "new-rs", "surge-pod", "probe-pass",
                 "endpoint-add", "old-terminate", "endpoint-remove"]
    by_id = {it["id"]: it for it in w["items"]}
    w["items"] = [by_id[i] for i in canonical]
    return lesson, model


def mut_order_contradicts_itself(lesson, model):
    """Declare a constraint the returned order does not satisfy."""
    return lesson, model + (
        "\n\n_orig_rollout_order = rollout_order\n"
        "def rollout_order():\n"
        "    r = _orig_rollout_order()\n"
        "    r['constraints'] = r['constraints'] + [['endpoint-remove', 'edit']]\n"
        "    return r\n"
    )


def mut_order_unconstrained(lesson, model):
    """No constraints at all, so every arrangement is 'correct'."""
    return lesson, model + (
        "\n\n_orig_rollout_order2 = rollout_order\n"
        "def rollout_order():\n"
        "    r = _orig_rollout_order2()\n"
        "    r['constraints'] = []\n"
        "    return r\n"
    )


def mut_order_incomplete(lesson, model):
    """Return an order missing one of the widget's items."""
    return lesson, model + (
        "\n\n_orig_rollout_order3 = rollout_order\n"
        "def rollout_order():\n"
        "    r = _orig_rollout_order3()\n"
        "    r['order'] = r['order'][:-1]\n"
        "    return r\n"
    )


def mut_order_asserted_answer(lesson, model):
    """Smuggle the answer into the config instead of deriving it."""
    find_widget(lesson, "order-build")["correct_order"] = ["edit", "new-rs"]
    return lesson, model


# --------------------------------------------------- mutations: bug-hunt --


def mut_bug_already_fixed(lesson, model):
    """Ship the corrected code, so there is no bug to find."""
    w = find_widget(lesson, "bug-hunt")
    w["code"] = w["code"].replace("serving >= floor", "serving > floor")
    return lesson, model


def mut_bug_two_lines_work(lesson, model):
    """Make a second candidate fix the tests too.

    Raising the floor by one compensates for the >= exactly, so both lines
    'work' and a learner who picks either is right. It takes dropping the
    assertion that pins the floor as well, which is the point: the exercise is
    unambiguous only because the tests pin the values the wrong fix would move.
    """
    w = find_widget(lesson, "bug-hunt")
    w["tests"] = "\n".join(l for l in w["tests"].split("\n") if 'r["floor"] == 10' not in l)
    for c in w["candidates"]:
        if c["id"] == "floor":
            c["patch"] = "    floor = replicas - max_unavailable + 1"
    return lesson, model


def mut_bug_no_line_works(lesson, model):
    """Break the one patch that fixed it, leaving the exercise unwinnable."""
    w = find_widget(lesson, "bug-hunt")
    for c in w["candidates"]:
        if c["id"] == "compare":
            c["patch"] = "    may_terminate = serving >= floor"
    return lesson, model


def mut_bug_line_out_of_range(lesson, model):
    """Point a candidate past the end of the listing."""
    find_widget(lesson, "bug-hunt")["candidates"][0]["line"] = 99
    return lesson, model


def mut_bug_asserted_answer(lesson, model):
    """Write the answer into the config instead of deriving it."""
    find_widget(lesson, "bug-hunt")["buggy_line"] = 5
    return lesson, model


SUITES = [
    (ORDER_LESSON, [
        ("bug-hunt ships already fixed",   mut_bug_already_fixed,     "no bug to find"),
        ("two lines both fix the tests",   mut_bug_two_lines_work,    "each fix the tests"),
        ("no line fixes the tests",        mut_bug_no_line_works,     "no right answer"),
        ("candidate line out of range",    mut_bug_line_out_of_range, "outside the"),
        ("bug answer asserted, not derived", mut_bug_asserted_answer, "must not assert an answer"),
        ("steps already in a valid order", mut_order_already_solved,   "already listed in a valid order"),
        ("order breaks its own constraint", mut_order_contradicts_itself, "breaks its own constraint"),
        ("no constraints, any order passes", mut_order_unconstrained,  "declares no constraints"),
        ("order omits an item",             mut_order_incomplete,      "not an arrangement"),
        ("order answer asserted, not derived", mut_order_asserted_answer, "must not assert an answer"),
    ]),
    (PY_LESSON, [
        ("two options both correct",     mut_two_answers,             "predicates are true"),
        ("no option is correct",         mut_no_answer,               "no correct answer"),
        ("answer asserted, not derived", mut_asserted_answer,         "must not assert an answer"),
        ("starter already solved",       mut_starter_already_correct, "not broken"),
        ("reference solution is wrong",  mut_broken_solution,         "does not pass its own checks"),
        ("chart labels mismatch values", mut_label_mismatch,          "mislabel"),
        ("readout returns NaN",          mut_nan_readout,             "call failed"),
        ("widget calls a missing fn",    mut_missing_function,        "no such function"),
        ("slider default out of range",  mut_bad_param_default,       "outside"),
        ("simulation never terminates",  mut_nonterminating_sim,      "never set done"),
        ("em-dash written into prose",   mut_em_dash_in_prose,        "em-dash in"),
        ("en-dash only in a rendered caption", mut_en_dash_in_caption, "en-dash in"),
        ("claimed primitive ignores temperature", mut_kernel_disagreement, "disagrees with the kernel"),
        ("claimed primitive scales the wrong way", mut_kernel_off_by_a_little, "disagrees with the kernel"),
        ("claim against a kernel that does not exist", mut_unknown_kernel, "not a kernel"),
    ]),
    (YAML_LESSON, [
        ("graded starter already passes", mut_graded_starter_passes,  "not broken"),
        ("graded solution is rejected",   mut_graded_solution_fails,  "rejected by its own grader"),
        ("grader returns wrong shape",    mut_grader_wrong_shape,     "'passed' boolean"),
        ("grader raises",                 mut_grader_raises,          "grader raised an error"),
        ("both code-cell modes declared", mut_both_modes,             "pick one mode"),
        ("rejection with no failing check", mut_grader_silent_rejection, "no indication of what is wrong"),
        ("grid columns mismatch headers", mut_sim_grid_mismatch,      "column labels"),
        ("version pin with an unreadable date", mut_undateable_pin,   "not a date"),
        ("citation with no link",          mut_unfollowable_citation, "needs a title and a url"),
    ]),
]


def run_case(slug: str, lesson: dict, model: str):
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        d = base / slug
        d.mkdir()
        (d / "lesson.json").write_text(json.dumps(lesson))
        (d / "model.py").write_text(model)
        return verify_lesson(slug, lessons_dir=base).items


def main() -> int:
    total_missed = 0
    total_cases = 0

    for slug, mutations in SUITES:
        src = LESSONS / slug
        base_lesson = json.loads((src / "lesson.json").read_text())
        base_model = (src / "model.py").read_text()

        print(f"\n{slug}")
        print(f"  control: unmutated lesson must pass")
        errors = [i for i in run_case(slug, copy.deepcopy(base_lesson), base_model) if i[0] == "ERROR"]
        if errors:
            print("    ✗ FAIL: the known-good lesson does not verify:")
            for _, where, msg in errors:
                print(f"        {where}: {msg}")
            total_missed += 1
        else:
            print("    ✓ passes cleanly")

        for name, mutate, expect in mutations:
            total_cases += 1
            lesson, model = mutate(copy.deepcopy(base_lesson), base_model)
            errors = [i for i in run_case(slug, lesson, model) if i[0] == "ERROR"]
            if any(expect.lower() in msg.lower() for _, _, msg in errors):
                print(f"    ✓ caught: {name}")
            else:
                total_missed += 1
                print(f"    ✗ MISSED: {name}")
                print(f"        expected an error containing {expect!r}")
                for _, where, msg in errors[:3]:
                    print(f"        actual: {where}: {msg[:140]}")
                if not errors:
                    print("        actual: no errors raised at all")

    print()
    if total_missed:
        print(f"{total_missed} failure(s) across {total_cases} mutations")
        return 1
    print(f"all {total_cases} mutations caught, both controls clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
