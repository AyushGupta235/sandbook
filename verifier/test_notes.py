"""Tests for the Obsidian note grounding hook.

Built against a synthetic vault, never the author's real one. A test that reads
someone's actual notes passes or fails for reasons that have nothing to do with
the code, and would quietly stop working on anyone else's machine.

The read-only guarantee gets its own test. It is the one property of this
module that would be genuinely costly to get wrong, since a vault is often the
only copy of someone's writing.

Run:  python3 verifier/test_notes.py   (or ./sandbook selftest)
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import notes  # noqa: E402


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


VAULT = {
    "Research Papers/LoRA.md": (
        "---\ntags: [ml]\n---\n\n"
        "Low-rank adaptation freezes the base weights and trains two small\n"
        "matrices. See [[Fine-tuning]] and [[Attention]].\n"
        "Also links to [[Nothing Written Yet]].\n"
    ),
    "Fine-tuning.md": "Full fine-tuning updates every weight. Related: [[LoRA]].\n",
    "Literature Notes/Attention.md": "Queries, keys and values.\n",
    "Daily/2026-01-01.md": "Unrelated note.\n",
    ".obsidian/workspace.json": "{}\n",
    ".trash/Deleted.md": "should never be read\n",
}


def build_vault(base: pathlib.Path) -> pathlib.Path:
    for rel, text in VAULT.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return base


def fingerprint(vault: pathlib.Path) -> dict[str, str]:
    return {str(p.relative_to(vault)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(vault.rglob("*")) if p.is_file()}


def test_discovery() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        vault = build_vault(pathlib.Path(tmp))
        found = {p.name for p in notes.all_notes(vault)}
        check("LoRA.md" in found, f"did not find the obvious note: {found}")
        check("Deleted.md" not in found, "read a note out of .trash")
        check(all(not n.endswith(".json") for n in found), "picked up a non-note file")
    return "finds notes, skips .obsidian and .trash"


def test_resolution() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        vault = build_vault(pathlib.Path(tmp))
        check(notes.resolve(vault, "Research Papers/LoRA.md").name == "LoRA.md",
              "failed to resolve an exact path")
        check(notes.resolve(vault, "LoRA").name == "LoRA.md",
              "failed to resolve by title")
        check(notes.resolve(vault, "lora").name == "LoRA.md",
              "title matching should not care about case")
        check(notes.resolve(vault, "Fine-tun").name == "Fine-tuning.md",
              "failed to resolve a partial title")
        for missing in ("nothing-like-this", ""):
            try:
                notes.resolve(vault, missing)
                raise Failure(f"resolving {missing!r} should have failed")
            except notes.NoteError:
                pass
    return "resolves by path, title, case and prefix; missing notes raise"


def test_ambiguity_is_reported() -> str:
    """Two notes with the same title must not be silently picked between."""
    with tempfile.TemporaryDirectory() as tmp:
        vault = build_vault(pathlib.Path(tmp))
        (vault / "Fleeting Notes").mkdir(parents=True, exist_ok=True)
        (vault / "Fleeting Notes" / "LoRA.md").write_text("a second note of the same name\n")
        try:
            notes.resolve(vault, "LoRA")
            raise Failure("two notes named LoRA should have been reported as ambiguous")
        except notes.NoteError as e:
            check("2 notes match" in str(e), f"unhelpful ambiguity message: {e}")
            check("Fleeting Notes" in str(e), "the message should list the candidates")
    return "ambiguous titles are reported with the candidates, never guessed"


def test_link_following() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        vault = build_vault(pathlib.Path(tmp))
        gathered = notes.gather(vault, "LoRA")
        titles = gathered["titles"]
        check(titles[0] == "LoRA", f"the named note should come first, got {titles}")
        check("Fine-tuning" in titles and "Attention" in titles,
              f"did not follow wikilinks: {titles}")
        check("Nothing Written Yet" not in titles,
              "a link to a note that does not exist should be skipped, not fatal")
        check("2026-01-01" not in titles, "pulled in an unlinked note")
        check(len(titles) == len(set(titles)), f"gathered a note twice: {titles}")

        alone = notes.gather(vault, "LoRA", follow_links=False)
        check(alone["titles"] == ["LoRA"], f"--no-links should stay put: {alone['titles']}")
    return "follows links one level, skips unwritten links, honours --no-links"


def test_grounding_text() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        vault = build_vault(pathlib.Path(tmp))
        text = notes.grounding_text(vault, notes.gather(vault, "LoRA"))
        check("freezes the base weights" in text, "the note's own words are missing")
        check("Queries, keys and values" in text, "a linked note's body is missing")
        check("not instruction" in text,
              "the block must tell the writer this is material, not orders")
        check("may be partial" in text,
              "the block must warn that notes are not a specification")
    return "renders bodies with the data-not-instruction caution"


def test_vault_is_never_written() -> str:
    """The guarantee that matters: reading notes cannot change them."""
    with tempfile.TemporaryDirectory() as tmp:
        vault = build_vault(pathlib.Path(tmp))
        before = fingerprint(vault)
        gathered = notes.gather(vault, "LoRA")
        notes.grounding_text(vault, gathered)
        notes.all_notes(vault)
        try:
            notes.resolve(vault, "does-not-exist")
        except notes.NoteError:
            pass
        after = fingerprint(vault)
        check(before == after,
              "the vault changed while being read: "
              f"{set(before) ^ set(after) or 'contents differ'}")
    return "the vault is byte-for-byte unchanged after a full read"


TESTS = [
    ("finds notes, skips app directories", test_discovery),
    ("resolves notes by path and title", test_resolution),
    ("ambiguous titles are reported", test_ambiguity_is_reported),
    ("follows wikilinks one level", test_link_following),
    ("grounding block is well formed", test_grounding_text),
    ("reading never writes to the vault", test_vault_is_never_written),
]


def main() -> int:
    print("note grounding tests")
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
    print(f"\n{'all note tests passed' if not failed else f'{failed} note test(s) failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
