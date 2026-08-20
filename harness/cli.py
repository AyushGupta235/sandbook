"""sandbook command line entry point.

    sandbook plan "<topic>"     design the outline and stop, before the costly part
    sandbook plan --edit <slug> revise a saved outline module by module
    sandbook probe <slug>       test what you know, and reshape the outline around it
    sandbook build "<topic>"    generate a lesson into output/
    sandbook build --from-plan <slug>   build an outline you approved
    sandbook promote <slug>     move a reviewed lesson from output/ into lessons/
    sandbook serve              serve the runtime on localhost
    sandbook verify [slug]      run the contract verifier (all lessons by default)
    sandbook selftest           run the verifier's own mutation suite
    sandbook list               list built lessons

Generated lessons land in a gitignored output/ directory. Promote one into
lessons/ once you have read it or worked through it, so unreviewed machine
output never reaches the repository.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import pathlib
import shutil
import socketserver
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LESSONS = ROOT / "lessons"
OUTPUT = ROOT / "output"
DEFAULT_PORT = 8765


# ------------------------------------------------------------------- helpers


def notes_env() -> str:
    sys.path.insert(0, str(ROOT / "harness"))
    import notes
    return notes.VAULT_ENV


def _lesson_dirs(base: pathlib.Path) -> list[pathlib.Path]:
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if (p / "lesson.json").exists())


def _title_of(path: pathlib.Path) -> str:
    try:
        return json.loads((path / "lesson.json").read_text()).get("title", "")
    except (OSError, json.JSONDecodeError):
        return ""


# ------------------------------------------------------------------ commands


def cmd_build(args: argparse.Namespace) -> int:
    # Both are optional individually, so guard the combination. Without this a
    # bare `sandbook build` starts a live run on an empty topic and spends real
    # money designing a lesson about nothing.
    if not args.topic and not args.from_plan:
        print("build needs a topic, or --from-plan <slug> to build a saved outline.\n"
              "  ./sandbook plan \"your topic\"     designs an outline first, for much less",
              file=sys.stderr)
        return 2
    if args.topic and args.from_plan:
        print("pass a topic or --from-plan, not both: a saved outline already has its topic",
              file=sys.stderr)
        return 2

    sys.path.insert(0, str(ROOT / "harness"))
    from llm import AgentSDKModel, ModelAuthError, ModelError, ScriptedModel, save_recording
    import pipeline

    icons = {"stage": "·", "curriculum": "▸", "ok": "✓", "repair": "↻",
             "drop": "✗", "fail": "✗", "detail": "   ", "wait": "⋯"}

    def on_retry(stage: str, attempt: int, delay: float, reason: str) -> None:
        print(f"  {icons['wait']} {stage}: {reason[:80]}, retrying in {delay:.0f}s "
              f"(attempt {attempt})", flush=True)

    recording: list = []
    if args.replay:
        model = ScriptedModel.from_file(args.replay)
        print(f"replaying {args.replay} (no model calls)")
    else:
        model = AgentSDKModel(record=recording, on_retry=on_retry)

    def on_event(kind: str, message: str) -> None:
        print(f"  {icons.get(kind, '·')} {message}", flush=True)

    grounding = ""
    if args.grounding:
        grounding = pathlib.Path(args.grounding).read_text()
    if args.from_note:
        import notes
        try:
            vault = notes.vault_path(args.vault)
            gathered = notes.gather(vault, args.from_note, follow_links=not args.no_links)
        except notes.NoteError as e:
            print(f"\n{e}", file=sys.stderr)
            return 2
        print(f"  · grounding in your notes: {', '.join(gathered['titles'])}")
        grounding = "\n\n".join(
            p for p in (grounding, notes.grounding_text(vault, gathered)) if p)

    saved = None
    if args.from_plan:
        try:
            saved = pipeline.load_plan(OUTPUT, args.from_plan)
        except ModelError as e:
            print(f"\n{e}", file=sys.stderr)
            return 1
        print(f"building from the outline you approved: {saved['title']}")
    else:
        print(f"building a lesson on: {args.topic}")

    try:
        report = pipeline.build(model, args.topic or "", output_root=OUTPUT,
                                grounding=grounding, ground=args.ground,
                                review=args.review, curriculum=saved,
                                on_event=on_event)
    except ModelAuthError as e:
        print(f"\n{e}", file=sys.stderr)
        return 2
    except ModelError as e:
        print(f"\nthe pipeline could not continue: {e}", file=sys.stderr)
        return 1
    finally:
        if recording and args.record:
            save_recording(args.record, recording)
            print(f"  · recorded {len(recording)} model call(s) to {args.record}")

    print()
    if not report.ok:
        print("FAILED: no lesson written." if report.path is None
              else f"FAILED: wrote {report.path} but it does not verify.")
        for mid, findings in report.dropped[:8]:
            print(f"  dropped {mid}: {findings[0][2][:140]}")
        return 1

    print(f"wrote {report.path.relative_to(ROOT)}")
    print(f"  {len(report.shipped)} module(s) shipped: {', '.join(report.shipped)}")
    if report.repairs:
        print(f"  {report.repairs} repair round(s) along the way")
    if report.dropped:
        print(f"  {len(report.dropped)} module(s) dropped rather than shipped broken:")
        for mid, findings in report.dropped:
            print(f"    {mid}: {findings[0][2][:120]}")
    if report.reviewed:
        print(f"  {report.reviewed} module(s) reviewed for false claims")
    if report.review_warnings:
        print(f"  {len(report.review_warnings)} review warning(s), not blocking:")
        for where, message in report.review_warnings[:5]:
            print(f"    {where}: {message[:110]}")
    if report.cost_usd:
        print(f"  cost: ${report.cost_usd:.2f}")
    if getattr(model, "retries", 0):
        print(f"  {model.retries} transient API failure(s) retried rather than dropped")
    _write_index(OUTPUT)
    print(f"\nread it:    ./sandbook serve   then open"
          f" ?lesson={report.slug}&from=output")
    print(f"promote it: ./sandbook promote {report.slug}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Design the outline, show it, and stop before the expensive part."""
    if not args.topic and not args.edit:
        print("plan needs a topic, or --edit <slug> to revise a saved outline.",
              file=sys.stderr)
        return 2
    if args.topic and args.edit:
        print("pass a topic or --edit, not both", file=sys.stderr)
        return 2

    sys.path.insert(0, str(ROOT / "harness"))
    from llm import AgentSDKModel, ModelAuthError, ModelError
    import pipeline

    model = AgentSDKModel()

    def on_event(kind: str, message: str) -> None:
        print(f"  {'✓' if kind == 'ok' else '·'} {message}", flush=True)

    try:
        if args.edit:
            curriculum = pipeline.load_plan(OUTPUT, args.edit)
            print(pipeline.outline_text(curriculum))
            instructions = _collect_edits(curriculum)
            if not instructions:
                print("nothing changed")
                return 0
            print("\n  · revising the outline")
            curriculum = pipeline.run_revise(model, curriculum, instructions)
            path = pipeline.save_plan(OUTPUT, curriculum)
            print()
            if curriculum.get("revision_note"):
                print(f"  {curriculum['revision_note']}\n")
            print(pipeline.outline_text(curriculum))
            print(f"saved to {path.relative_to(ROOT)}")
        else:
            grounding = pathlib.Path(args.grounding).read_text() if args.grounding else ""
            curriculum = pipeline.plan(model, args.topic, output_root=OUTPUT,
                                       grounding=grounding, ground=args.ground,
                                       on_event=on_event)
            print()
            print(pipeline.outline_text(curriculum))
    except ModelAuthError as e:
        print(f"\n{e}", file=sys.stderr)
        return 2
    except ModelError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1

    if getattr(model, "total_cost_usd", 0.0):
        print(f"cost: ${model.total_cost_usd:.2f}")
    slug = curriculum["slug"]
    print(f"\nedit it:  ./sandbook plan --edit {slug}"
          f"\n      or  $EDITOR output/{slug}/curriculum.json"
          f"\nbuild it: ./sandbook build --from-plan {slug}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Find out what the reader already knows, then reshape the outline."""
    sys.path.insert(0, str(ROOT / "harness"))
    from llm import AgentSDKModel, ModelAuthError, ModelError
    import pipeline
    import learner_profile as prof

    model = AgentSDKModel()
    try:
        curriculum = pipeline.load_plan(OUTPUT, args.slug)
        print(f"{curriculum['title']}: {len(curriculum['modules'])} modules planned")
        print("  · writing questions\n", flush=True)
        questions = pipeline.run_probe(model, curriculum)
    except ModelAuthError as e:
        print(f"\n{e}", file=sys.stderr)
        return 2
    except ModelError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1

    by_id = {m["id"]: m for m in curriculum["modules"]}
    results, answered = [], 0
    print("Answer honestly. Getting one wrong is the point: it is how the lesson")
    print("finds what to spend its time on. Enter to skip a question.\n")

    for i, q in enumerate(questions, 1):
        print(f"  {i}. {q['question']}")
        for j, option in enumerate(q["options"]):
            print(f"       {chr(97 + j)}) {option}")
        try:
            answer = input("     > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not answer:
            print()
            continue
        picked = ord(answer[0]) - 97 if answer[0].isalpha() else -1
        correct = picked == q["answer_index"]
        answered += 1
        print(f"     {'correct' if correct else 'not quite'}. {q['why']}\n")
        module = by_id.get(q["module_id"], {})
        results.append({"module_id": q["module_id"], "question": q["question"],
                        "misconception": module.get("misconception") or module.get("intent", ""),
                        "correct": correct})

    if not answered:
        print("nothing answered, so the outline is unchanged")
        return 0

    right = sum(1 for r in results if r["correct"])
    print(f"{right} of {answered} correct.")

    if not args.no_profile:
        path = prof.record(results, curriculum.get("_topic", args.slug))
        print(f"recorded to {path}")

    instructions = pipeline.probe_instructions(results, curriculum)
    if not instructions:
        print("nothing to change")
        return 0

    print("\n  · reshaping the outline around what you missed", flush=True)
    try:
        revised = pipeline.run_revise(model, curriculum, instructions)
    except ModelError as e:
        print(f"\nthe outline could not be revised: {e}", file=sys.stderr)
        return 1

    dropped = {m["id"] for m in curriculum["modules"]} - {m["id"] for m in revised["modules"]}
    pipeline.save_plan(OUTPUT, revised)
    print()
    if revised.get("revision_note"):
        print(f"  {revised['revision_note']}\n")
    if dropped:
        # Never silent. A module removed on this evidence is a module the
        # reader never sees, so they get told which ones and why.
        print("  dropped because you answered their question correctly:")
        for mid in sorted(dropped):
            print(f"    {mid}: {by_id[mid]['title']}")
        print()
    print(pipeline.outline_text(revised))
    if getattr(model, "total_cost_usd", 0.0):
        print(f"cost: ${model.total_cost_usd:.2f}")
    print(f"build it: ./sandbook build --from-plan {revised['slug']}")
    return 0


def _collect_edits(curriculum: dict) -> str:
    """Walk the outline module by module and collect what the reader wants.

    Deliberately a plain prompt loop. The alternative is a form, and a form
    would take longer to build than it saves anyone.
    """
    print("For each module: [k]eep, [d]rop, [x] I already know this, "
          "[+] go deeper, or type a note.\n")
    notes = []
    for i, m in enumerate(curriculum.get("modules") or [], 1):
        try:
            answer = input(f"  {i}. {m['title']} [{m['widget_type']}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""
        low = answer.lower()
        if low in ("", "k", "keep"):
            continue
        if low in ("d", "drop"):
            notes.append(f"Drop the module '{m['title']}' (id {m['id']}).")
        elif low in ("x",):
            notes.append(f"I already understand this, so drop '{m['title']}' (id {m['id']}): "
                         f"{m.get('misconception') or m['intent']}")
        elif low in ("+", "deeper"):
            notes.append(f"Go deeper on '{m['title']}' (id {m['id']}); the current plan is "
                         "too shallow for me.")
        else:
            notes.append(f"About '{m['title']}' (id {m['id']}): {answer}")

    try:
        extra = input("\n  Anything to add that is missing? > ").strip()
    except (EOFError, KeyboardInterrupt):
        extra = ""
    if extra:
        notes.append(f"Add coverage of: {extra}")
    return "\n".join(f"- {n}" for n in notes)


def cmd_review(args: argparse.Namespace) -> int:
    """Ask a fresh context whether a finished lesson teaches anything false."""
    sys.path.insert(0, str(ROOT / "harness"))
    from llm import AgentSDKModel, ModelAuthError, ModelError
    import pipeline

    base = OUTPUT if args.from_output else LESSONS
    slugs = args.slug or [p.name for p in _lesson_dirs(base)]
    if not slugs:
        print(f"no lessons in {base.name}/", file=sys.stderr)
        return 1

    icons = {"stage": "·", "ok": "✓", "drop": "✗", "detail": "!"}
    model = AgentSDKModel()
    grounding = pathlib.Path(args.grounding).read_text() if args.grounding else ""

    errors = warnings = 0
    for slug in slugs:
        print(f"\n{slug}")
        try:
            findings = pipeline.review_lesson(
                model, slug, base, grounding,
                on_event=lambda kind, msg: print(f"  {icons.get(kind, '·')} {msg}", flush=True))
        except ModelAuthError as e:
            print(f"\n{e}", file=sys.stderr)
            return 2
        except ModelError as e:
            print(f"  review could not run: {e}", file=sys.stderr)
            return 1
        errors += sum(1 for s, _, _ in findings if s == "ERROR")
        warnings += len(findings) - sum(1 for s, _, _ in findings if s == "ERROR")

    print(f"\n{errors} objection(s), {warnings} warning(s)")
    if getattr(model, "total_cost_usd", 0.0):
        print(f"cost: ${model.total_cost_usd:.2f}")
    print("\nThese are one model's opinions, not verifier findings. Read them "
          "before acting on any of them.")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    source = OUTPUT / args.slug
    if not (source / "lesson.json").exists():
        print(f"no generated lesson at {source}", file=sys.stderr)
        return 1
    destination = LESSONS / args.slug
    if destination.exists() and not args.force:
        print(f"{destination} already exists; pass --force to replace it", file=sys.stderr)
        return 1

    # Copy first, then verify in place. A lesson that fails here is removed
    # again rather than left half-promoted.
    backup = None
    if destination.exists():
        backup = pathlib.Path(shutil.make_archive(
            str(destination) + ".backup", "zip", root_dir=destination))
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

    if subprocess.call([sys.executable, str(ROOT / "verifier" / "verify.py"), args.slug]) != 0:
        shutil.rmtree(destination)
        if backup:
            shutil.unpack_archive(str(backup), str(destination))
            backup.unlink()
        print("\npromotion reverted: the lesson does not pass the verifier", file=sys.stderr)
        return 1
    if backup:
        backup.unlink()

    _write_index(LESSONS)
    _write_index(OUTPUT)
    print(f"promoted to {destination.relative_to(ROOT)} and refreshed the lesson index")
    return 0


def _write_index(base: pathlib.Path) -> None:
    """Rewrite the index the runtime's library page reads."""
    lessons = []
    for path in _lesson_dirs(base):
        data = json.loads((path / "lesson.json").read_text())
        lessons.append({"slug": path.name,
                        "title": data.get("title", path.name),
                        "subtitle": data.get("subtitle", "")})
    base.mkdir(parents=True, exist_ok=True)
    (base / "index.json").write_text(json.dumps({"lessons": lessons}, indent=2) + "\n")


def cmd_serve(args: argparse.Namespace) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    url = f"http://localhost:{args.port}/runtime/index.html"
    with Server(("127.0.0.1", args.port), handler) as httpd:
        print(f"sandbook serving {ROOT}")
        print(f"  library : {url}")
        for path in _lesson_dirs(LESSONS):
            print(f"  lesson  : {url}?lesson={path.name}")
        print("\nctrl-c to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    return subprocess.call(
        [sys.executable, str(ROOT / "verifier" / "verify.py")] + args.slug)


def cmd_selftest(_args: argparse.Namespace) -> int:
    """Prove all three halves still work: the kernels behave as claimed, the
    verifier catches planted defects, and the pipeline repairs what it can and
    drops what it cannot. Kernels run first; if they are wrong, everything
    measured against them is wrong too."""
    rc = subprocess.call([sys.executable, str(ROOT / "kernels" / "test_kernels.py")])
    rc |= subprocess.call([sys.executable, str(ROOT / "verifier" / "test_notes.py")])
    rc |= subprocess.call([sys.executable, str(ROOT / "verifier" / "test_profile.py")])
    rc |= subprocess.call([sys.executable, str(ROOT / "verifier" / "test_mutations.py")])
    rc |= subprocess.call([sys.executable, str(ROOT / "verifier" / "test_pipeline.py")])
    return rc


def cmd_list(_args: argparse.Namespace) -> int:
    rows = [("lessons/", p) for p in _lesson_dirs(LESSONS)]
    rows += [("output/", p) for p in _lesson_dirs(OUTPUT)]
    if not rows:
        print("no lessons yet")
        return 0
    width = max(len(p.name) for _, p in rows)
    for where, path in rows:
        data = json.loads((path / "lesson.json").read_text())
        n = len(data.get("modules", []))
        print(f"  {where:<9} {path.name.ljust(width)}  {n} modules  {_title_of(path)}")
    return 0


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbook", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="design the outline and stop, before the costly part")
    p_plan.add_argument("topic", nargs="?", help="what the lesson should teach")
    p_plan.add_argument("--edit", metavar="SLUG",
                        help="revise a saved outline module by module")
    p_plan.add_argument("--ground", action="store_true",
                        help="look up and cite versioned sources first")
    p_plan.add_argument("--grounding", help="file of source material to design against")
    p_plan.set_defaults(func=cmd_plan)

    p_probe = sub.add_parser("probe", help="test what you already know, and reshape the outline")
    p_probe.add_argument("slug", help="a lesson outline saved by `sandbook plan`")
    p_probe.add_argument("--no-profile", action="store_true",
                         help="do not record the answers for future lessons")
    p_probe.set_defaults(func=cmd_probe)

    p_build = sub.add_parser("build", help="generate a lesson into output/")
    p_build.add_argument("topic", nargs="?", help="what the lesson should teach")
    p_build.add_argument("--from-plan", metavar="SLUG",
                         help="build the outline saved by `sandbook plan`, skipping "
                              "the design stage")
    p_build.add_argument("--grounding", help="file of source notes to ground the lesson in")
    p_build.add_argument("--from-note", metavar="NOTE",
                         help="ground the lesson in one of your own Obsidian notes, "
                              "named by path or title (read-only)")
    p_build.add_argument("--vault", help=f"path to the vault (default: ${notes_env()})")
    p_build.add_argument("--no-links", action="store_true",
                         help="use only the named note, not the notes it links to")
    p_build.add_argument("--ground", action="store_true",
                         help="look up and cite versioned sources before writing "
                              "(the only stage that reaches the network)")
    p_build.add_argument("--review", action="store_true",
                         help="have a fresh context check each module for false claims "
                              "(slower, and roughly doubles the cost)")
    p_build.add_argument("--record", help="save the model calls to this file for replay")
    p_build.add_argument("--replay", help="replay a recording instead of calling the model")
    p_build.set_defaults(func=cmd_build)

    p_promote = sub.add_parser("promote", help="move a reviewed lesson into lessons/")
    p_promote.add_argument("slug")
    p_promote.add_argument("--force", action="store_true", help="replace an existing lesson")
    p_promote.set_defaults(func=cmd_promote)

    p_review = sub.add_parser("review", help="check a finished lesson for false claims")
    p_review.add_argument("slug", nargs="*", help="lesson slugs (default: all)")
    p_review.add_argument("--from-output", action="store_true",
                          help="review a draft in output/ rather than a promoted lesson")
    p_review.add_argument("--grounding", help="file of source material to check claims against")
    p_review.set_defaults(func=cmd_review)

    p_serve = sub.add_parser("serve", help="serve the runtime on localhost")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.set_defaults(func=cmd_serve)

    p_verify = sub.add_parser("verify", help="run the contract verifier")
    p_verify.add_argument("slug", nargs="*", help="lesson slugs (default: all)")
    p_verify.set_defaults(func=cmd_verify)

    sub.add_parser("selftest", help="prove the verifier catches planted defects").set_defaults(func=cmd_selftest)
    sub.add_parser("list", help="list built lessons").set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
