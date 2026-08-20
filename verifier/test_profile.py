"""Tests for the knowledge probe and the profile it writes.

The profile decides which modules a reader never sees, so its failure mode is
silent: a wrong entry removes something they needed and nothing tells them. The
tests below are mostly about the guards against that, not the happy path.

Run:  python3 verifier/test_profile.py   (or ./sandbook selftest)
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import learner_profile as prof  # noqa: E402
import pipeline  # noqa: E402


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def results(*pairs) -> list[dict]:
    return [{"module_id": mid, "misconception": f"thinks {mid} is simple",
             "question": f"what about {mid}?", "correct": ok} for mid, ok in pairs]


def test_records_both_outcomes() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "profile.json"
        prof.record(results(("a", True), ("b", False)), "a-topic", path)
        stored = json.loads(path.read_text())["demonstrated"]
        check(len(stored) == 2, f"both outcomes should be kept, got {stored}")
        check({e["correct"] for e in stored} == {True, False},
              "a wrong answer is a record worth keeping too")
        # Only the correct one counts as established.
        live = prof.established(path)
        check([e["module_id"] for e in live] == ["a"],
              f"only correct answers are established: {live}")
    return "keeps right and wrong answers, counts only the right ones"


def test_entries_expire() -> str:
    """Knowledge decays, so an old right answer stops counting."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "profile.json"
        prof.record(results(("a", True)), "a-topic", path)
        today = datetime.date.today()
        check(len(prof.established(path, today)) == 1, "a fresh answer should count")
        just_inside = today + datetime.timedelta(days=prof.STALE_DAYS)
        check(len(prof.established(path, just_inside)) == 1,
              "an answer exactly at the limit still counts")
        past = today + datetime.timedelta(days=prof.STALE_DAYS + 1)
        check(prof.established(path, past) == [],
              "an answer past the limit must be asked again, not assumed")
    return f"an answer stops counting after {prof.STALE_DAYS} days"


def test_survives_a_corrupt_file() -> str:
    """A damaged profile costs redundant modules, not the build."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "profile.json"
        path.write_text("{not json at all")
        check(prof.load(path)["demonstrated"] == [], "a corrupt profile should read as empty")
        check(prof.established(path) == [], "a corrupt profile establishes nothing")
        prof.record(results(("a", True)), "t", path)
        check(len(prof.load(path)["demonstrated"]) == 1, "it should be writable again after")
    return "a corrupt profile reads as empty rather than stopping the build"


def test_rewrites_rather_than_duplicates() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "profile.json"
        prof.record(results(("a", False)), "t", path)
        prof.record(results(("a", True)), "t", path)
        stored = json.loads(path.read_text())["demonstrated"]
        check(len(stored) == 1, f"answering again should update, not append: {stored}")
        check(stored[0]["correct"] is True, "the later answer should win")
    return "answering the same thing again updates the record"


def test_known_text_is_explicit() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "profile.json"
        prof.record(results(("cache", True)), "kv-caching", path)
        text = prof.known_text(prof.established(path))
        check("thinks cache is simple" in text, "the established statement is missing")
        check("Do not spend a module" in text, "the instruction to skip it is missing")
        check(prof.known_text([]) == "", "no entries should render nothing at all")
    return "established knowledge renders with an explicit instruction to build past it"


def test_probe_instructions_split_by_outcome() -> str:
    """Right answers drop a module, wrong answers expand it."""
    curriculum = {"modules": [
        {"id": "known", "title": "Known Thing", "widget_type": "step-sim",
         "intent": "i", "teaching_note": "n", "misconception": "thinks known is simple"},
        {"id": "shaky", "title": "Shaky Thing", "widget_type": "step-sim",
         "intent": "i", "teaching_note": "n", "misconception": "thinks shaky is simple"},
    ]}
    text = pipeline.probe_instructions(results(("known", True), ("shaky", False)), curriculum)
    check("Drop 'Known Thing'" in text, f"a correct answer should drop its module:\n{text}")
    check("Keep and expand 'Shaky Thing'" in text,
          f"a wrong answer should expand its module:\n{text}")
    check("what about shaky?" in text,
          "the missed question should become the module's opening")
    # A result for a module that is no longer in the outline is ignored, not fatal.
    stray = pipeline.probe_instructions(results(("gone", True)), curriculum)
    check(stray == "", f"a result for an unknown module should be ignored: {stray!r}")
    return "correct drops, wrong expands and carries its question, unknown ids ignored"


def test_levels_differ_in_kind() -> str:
    """A level has to change the brief, not just the module count."""
    seen = set()
    for name in pipeline.LEVELS:
        text = pipeline.level_text(name)
        check(name in text, f"{name} does not name itself in its brief")
        check("Assume:" in text and "Aim at:" in text,
              f"{name} states no assumption or target")
        seen.add(pipeline.LEVELS[name]["targets"])
    check(len(seen) == len(pipeline.LEVELS),
          "two levels share a target, so they would produce the same lesson")

    expert = pipeline.level_text("expert")
    orientation = pipeline.level_text("orientation")
    check("counterintuitive" in expert, "expert should aim at what is counterintuitive")
    check("only heard the name" in orientation, "orientation should assume no prior use")
    check("not about how many" in pipeline.level_text("deep"),
          "every level should say depth is not a padding knob")

    # An unknown level falls back rather than raising: a typo should not stop a build.
    check(pipeline.DEFAULT_LEVEL in pipeline.level_text("nonsense"),
          "an unknown level should fall back to the default")

    with_assume = pipeline.level_text("working", "I use Kubernetes daily, never Helm")
    check("never Helm" in with_assume, "the reader's own words are missing")
    check("outranks" in with_assume, "--assume must be stated as outranking the level")
    return "each level assumes and targets something different; --assume outranks it"


TESTS = [
    ("levels differ in kind, not just size", test_levels_differ_in_kind),
    ("records both outcomes", test_records_both_outcomes),
    ("entries expire", test_entries_expire),
    ("survives a corrupt file", test_survives_a_corrupt_file),
    ("re-answering updates", test_rewrites_rather_than_duplicates),
    ("established knowledge is explicit", test_known_text_is_explicit),
    ("probe outcomes drive revisions", test_probe_instructions_split_by_outcome),
]


def main() -> int:
    print("probe and profile tests")
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
    print(f"\n{'all probe tests passed' if not failed else f'{failed} probe test(s) failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
