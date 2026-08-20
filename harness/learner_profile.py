"""What the reader has already shown they understand.

A record of misconceptions answered correctly, with dates, so later lessons can
skip ground already covered. This is the part that compounds: the tenth lesson
should be shorter than the first because it knows what the first nine
established.

Three rules keep it honest, and all three exist because the failure mode here is
silent. A wrong entry removes content the reader needed, and they never find out
what they missed.

1. **Evidence, not assertion.** An entry means a question was answered
   correctly on a date, not that the reader "knows" something.
2. **It expires.** Anything older than `STALE_DAYS` stops counting and is asked
   again. Knowledge decays, and a two-year-old right answer is not evidence
   about today.
3. **It never acts silently.** Callers are expected to show what was skipped
   because of this file. `--no-profile` ignores it entirely.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

STALE_DAYS = 182                       # about six months
PROFILE_ENV = "SANDBOOK_PROFILE"


def profile_path() -> pathlib.Path:
    override = os.environ.get(PROFILE_ENV)
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".sandbook" / "profile.json"


def load(path: pathlib.Path | None = None) -> dict:
    path = path or profile_path()
    if not path.exists():
        return {"version": 1, "demonstrated": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # A corrupt profile must not stop a build. Losing the record costs a
        # few redundant modules; refusing to run costs the lesson.
        return {"version": 1, "demonstrated": []}
    data.setdefault("demonstrated", [])
    return data


def save(data: dict, path: pathlib.Path | None = None) -> pathlib.Path:
    path = path or profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def record(results: list[dict], topic: str, path: pathlib.Path | None = None) -> pathlib.Path:
    """Store the outcome of a probe.

    Both right and wrong answers are kept. A wrong answer is the more useful
    record of the two: it says this ground was shaky on a date, which is worth
    knowing when the same topic comes round again.
    """
    data = load(path)
    today = datetime.date.today().isoformat()
    by_key = {(e.get("topic"), e.get("misconception")): e for e in data["demonstrated"]}
    for r in results:
        key = (topic, r["misconception"])
        by_key[key] = {
            "topic": topic,
            "module_id": r.get("module_id", ""),
            "misconception": r["misconception"],
            "correct": bool(r["correct"]),
            "answered_on": today,
        }
    data["demonstrated"] = sorted(
        by_key.values(), key=lambda e: (e["topic"] or "", e["misconception"]))
    return save(data, path)


def established(path: pathlib.Path | None = None, today: datetime.date | None = None) -> list[dict]:
    """Entries answered correctly and still recent enough to count."""
    today = today or datetime.date.today()
    out = []
    for entry in load(path).get("demonstrated", []):
        if not entry.get("correct"):
            continue
        try:
            age = (today - datetime.date.fromisoformat(entry.get("answered_on", ""))).days
        except ValueError:
            continue
        if age <= STALE_DAYS:
            out.append(entry)
    return out


def known_text(entries: list[dict]) -> str:
    """Render established knowledge for the curriculum prompt."""
    if not entries:
        return ""
    lines = [
        "## Already established",
        "",
        "This reader has answered questions correctly showing they understand the",
        "following. Do not spend a module re-teaching any of it. Reference it",
        "freely as known ground, and build past it.",
        "",
    ]
    lines += [f"- {e['misconception']}  (shown on {e['answered_on']}, topic: {e['topic']})"
              for e in entries]
    return "\n".join(lines)
