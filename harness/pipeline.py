"""The generation pipeline: topic in, verified lesson out.

Stages run in a fixed order and nothing is open-ended. The model is asked for
data and pure functions; the harness does the assembling, verifying, repairing
and writing. A module that cannot be made to pass the verifier is dropped and
reported rather than shipped, so a lesson on disk has always cleared the same
bar as a hand-written one.

Each module is verified as it is built, in a probe lesson that renders only
that module's widget but carries the functions of every module accepted before
it. Rendering one widget keeps defect attribution exact, so the repair loop can
send one module's findings back without disturbing modules that were fine;
carrying the earlier functions means a module may reuse a helper an earlier one
defined. The assembled lesson is then verified as a whole, which catches what
only appears in combination, such as two modules claiming the same function
name.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verifier"))

from verify import verify_lesson  # noqa: E402
from llm import Model, ModelAuthError, ModelError, parse_json_reply  # noqa: E402

PROMPTS = ROOT / "harness" / "prompts"
MAX_REPAIR_ROUNDS = 3
MODULE_BUILD_ATTEMPTS = 2   # a reply in the wrong shape earns one more ask

WIDGET_TYPES = {"param-playground", "predict-reveal", "step-sim", "code-cell"}


# ----------------------------------------------------------------- rendering


def render(template_name: str, **values: str) -> str:
    """Fill {placeholders} in a prompt file.

    Plain string replacement rather than str.format, because the prompts are
    full of JSON braces that format() would choke on.
    """
    text = (PROMPTS / template_name).read_text()
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "lesson"


# ------------------------------------------------------------------- schemas


CURRICULUM_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "targets": {"type": ["string", "null"]},
        "packages": {"type": "array", "items": {"type": "string"}},
        "objectives": {"type": "array", "items": {"type": "string"}},
        "misconceptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"claim": {"type": "string"}, "reality": {"type": "string"}},
                "required": ["claim", "reality"],
                "additionalProperties": False,
            },
        },
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "widget_type": {"type": "string",
                                    "enum": sorted(WIDGET_TYPES)},
                    "intent": {"type": "string"},
                    "teaching_note": {"type": "string"},
                },
                "required": ["id", "title", "widget_type", "intent", "teaching_note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["slug", "title", "subtitle", "objectives", "misconceptions", "modules"],
    "additionalProperties": False,
}


# --------------------------------------------------------------- data shapes


@dataclass
class BuiltModule:
    spec: dict                      # the curriculum's brief for this module
    prose: str
    widget: dict
    functions: list[dict]           # [{name, source}]
    attempts: int = 1
    findings: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.spec["id"]


@dataclass
class BuildReport:
    slug: str
    title: str
    path: pathlib.Path | None
    shipped: list[str] = field(default_factory=list)
    dropped: list[tuple[str, list]] = field(default_factory=list)
    repairs: int = 0
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.path is not None and bool(self.shipped)


# ------------------------------------------------------------------- writing


def lesson_document(curriculum: dict, modules: list[BuiltModule]) -> dict:
    doc = {
        "schema_version": 1,
        "slug": curriculum["slug"],
        "title": curriculum["title"],
        "subtitle": curriculum.get("subtitle", ""),
        "model": "model.py",
        "packages": curriculum.get("packages") or [],
        "objectives": curriculum.get("objectives") or [],
        "misconceptions": curriculum.get("misconceptions") or [],
        "modules": [
            {"id": m.id, "title": m.spec["title"], "prose": m.prose, "widget": m.widget}
            for m in modules
        ],
    }
    if curriculum.get("targets"):
        doc["targets"] = curriculum["targets"]
    if curriculum.get("sources"):
        doc["sources"] = curriculum["sources"]
    return doc


def model_source(curriculum: dict, modules: list[BuiltModule]) -> str:
    header = [
        f'"""Model functions for "{curriculum["title"]}".',
        "",
        "Generated by sandbook. Every function here is pure: JSON in, JSON out,",
        "no I/O, no global mutation. The verifier runs these in CPython and the",
        "browser runs them in Pyodide, through the same bootstrap.",
        '"""',
        "",
    ]
    imports = sorted({"import math"} | {
        f"import {p}" for p in _import_names(curriculum.get("packages") or [])
    })
    parts = ["\n".join(header), "\n".join(imports), ""]
    for module in modules:
        parts.append(f"# {'-' * 24} {module.id}")
        for fn in module.functions:
            parts.append(fn["source"].rstrip() + "\n")
    return "\n\n".join(parts).rstrip() + "\n"


def _import_names(packages: list[str]) -> set[str]:
    mapping = {"pyyaml": "yaml", "pillow": "PIL", "scikit-learn": "sklearn"}
    return {mapping.get(p.lower(), p) for p in packages}


def write_lesson(directory: pathlib.Path, curriculum: dict, modules: list[BuiltModule],
                 function_modules: list[BuiltModule] | None = None) -> None:
    """Write a lesson. `function_modules` lets model.py carry functions from
    modules that are not themselves being rendered, which is what makes it
    possible to verify one module while it still reaches helpers defined by an
    earlier one."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "lesson.json").write_text(
        json.dumps(lesson_document(curriculum, modules), indent=2) + "\n")
    (directory / "model.py").write_text(
        model_source(curriculum, function_modules if function_modules is not None else modules))


# -------------------------------------------------------------------- stages


def run_curriculum(model: Model, topic: str, grounding: str = "") -> dict:
    reply = model.complete(
        stage="curriculum",
        system="You design interactive technical lessons. You return JSON only.",
        prompt=render("curriculum.md", topic=topic,
                      grounding=grounding or "No grounding notes supplied."),
        schema=CURRICULUM_SCHEMA,
        model="claude-opus-5",
    )
    curriculum = parse_json_reply(reply.text)
    _check_curriculum(curriculum, topic)
    return curriculum


def _check_curriculum(c: dict, topic: str) -> None:
    for field_name in ("title", "modules"):
        if not c.get(field_name):
            raise ModelError(f"curriculum is missing {field_name!r}")
    c.setdefault("slug", slugify(topic))
    c["slug"] = slugify(c["slug"])
    seen = set()
    for i, m in enumerate(c["modules"]):
        for key in ("id", "title", "widget_type", "intent", "teaching_note"):
            if not m.get(key):
                raise ModelError(f"module {i} is missing {key!r}")
        m["id"] = slugify(m["id"])
        if m["id"] in seen:
            raise ModelError(f"duplicate module id {m['id']!r}")
        seen.add(m["id"])
        if m["widget_type"] not in WIDGET_TYPES:
            raise ModelError(f"module {m['id']!r} has unknown widget type {m['widget_type']!r}")


def _module_prompt(template: str, curriculum: dict, spec: dict,
                   taken: set[str], grounding: str, **extra: str) -> str:
    return render(
        template,
        title=curriculum["title"],
        subtitle=curriculum.get("subtitle", ""),
        objectives="\n".join(f"- {o}" for o in curriculum.get("objectives") or []),
        misconceptions="\n".join(
            f"- believed: {m['claim']}  |  actually: {m['reality']}"
            for m in curriculum.get("misconceptions") or []),
        module_id=spec["id"],
        module_title=spec["title"],
        widget_type=spec["widget_type"],
        intent=spec["intent"],
        teaching_note=spec["teaching_note"],
        taken_names=", ".join(sorted(taken)) or "(none yet)",
        grounding=grounding or "",
        **extra,
    )


def run_module(model: Model, curriculum: dict, spec: dict,
               taken: set[str], grounding: str = "") -> BuiltModule:
    reply = model.complete(
        stage="module",
        system="You build one module of an interactive lesson. You return JSON only.",
        prompt=_module_prompt("module.md", curriculum, spec, taken, grounding),
        model="claude-sonnet-5",
    )
    return _parse_module(reply.text, spec)


def run_repair(model: Model, curriculum: dict, module: BuiltModule,
               findings: list, taken: set[str], grounding: str = "") -> BuiltModule:
    previous = json.dumps(
        {"prose": module.prose, "widget": module.widget, "functions": module.functions},
        indent=2)
    reply = model.complete(
        stage="repair",
        system="You repair a rejected lesson module. You return JSON only.",
        prompt=_module_prompt("repair.md", curriculum, module.spec, taken, grounding,
                              findings=_format_findings(findings), previous=previous),
        model="claude-opus-5",
    )
    repaired = _parse_module(reply.text, module.spec)
    repaired.attempts = module.attempts + 1
    return repaired


def _parse_module(text: str, spec: dict) -> BuiltModule:
    data = parse_json_reply(text)
    for key in ("prose", "widget", "functions"):
        if key not in data:
            raise ModelError(f"module reply is missing {key!r}")
    if not isinstance(data["functions"], list) or not data["functions"]:
        raise ModelError("module reply has no functions")
    for fn in data["functions"]:
        if not isinstance(fn, dict) or "name" not in fn or "source" not in fn:
            raise ModelError("each function needs a 'name' and a 'source'")
        if f"def {fn['name']}" not in fn["source"]:
            raise ModelError(f"function {fn['name']!r} does not define a matching def")
    widget = data["widget"]
    if not isinstance(widget, dict) or widget.get("type") != spec["widget_type"]:
        raise ModelError(
            f"widget type is {widget.get('type') if isinstance(widget, dict) else '?'!r}, "
            f"expected {spec['widget_type']!r}")
    return BuiltModule(spec=spec, prose=data["prose"], widget=widget,
                       functions=data["functions"])


def _format_findings(findings: list) -> str:
    return "\n".join(f"- [{sev}] {where}: {msg}" for sev, where, msg in findings) or "(none)"


# -------------------------------------------------------------- verification


def verify_modules(curriculum: dict, modules: list[BuiltModule],
                   slug: str | None = None,
                   function_modules: list[BuiltModule] | None = None) -> list:
    """Verify a lesson made of these modules. Returns the verifier's findings."""
    slug = slug or curriculum["slug"]
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        scratch = dict(curriculum, slug=slug)
        write_lesson(base / slug, scratch, modules, function_modules)
        return verify_lesson(slug, lessons_dir=base).items


def verify_candidate(curriculum: dict, accepted: list[BuiltModule],
                     candidate: BuiltModule) -> list:
    """Check one module against the verifier, with every already-accepted
    module's functions in scope. Only the candidate's own widget is rendered,
    so every finding belongs to it."""
    return errors_only(verify_modules(
        curriculum, [candidate], slug=f"probe-{candidate.id}",
        function_modules=accepted + [candidate]))


def errors_only(findings: list) -> list:
    return [f for f in findings if f[0] == "ERROR"]


# ------------------------------------------------------------------ the build


def build(model: Model, topic: str, *, output_root: pathlib.Path,
          grounding: str = "", on_event=lambda *_: None) -> BuildReport:
    on_event("stage", "designing the curriculum")
    curriculum = run_curriculum(model, topic, grounding)
    slug = curriculum["slug"]
    report = BuildReport(slug=slug, title=curriculum["title"], path=None)
    on_event("curriculum", f"{curriculum['title']}: {len(curriculum['modules'])} modules")

    built: list[BuiltModule] = []
    taken: set[str] = set()

    for spec in curriculum["modules"]:
        on_event("stage", f"building module {spec['id']} ({spec['widget_type']})")
        # A reply in the wrong shape is worth one more ask. It says nothing
        # about whether the module is teachable, and the repair loop below
        # cannot help: it needs a parsed module to repair.
        module = None
        for attempt in range(1, MODULE_BUILD_ATTEMPTS + 1):
            try:
                module = run_module(model, curriculum, spec, taken, grounding)
                break
            except ModelAuthError:
                raise            # not this module's fault, and every later one will fail too
            except ModelError as e:
                if attempt < MODULE_BUILD_ATTEMPTS:
                    on_event("repair", f"{spec['id']}: {e}, asking again")
                    continue
                on_event("drop", f"{spec['id']}: could not be built ({e})")
                report.dropped.append((spec["id"], [("ERROR", spec["id"], str(e))]))
        if module is None:
            continue

        module = _repair_until_clean(model, curriculum, module, taken, grounding,
                                     report, on_event, accepted=built)
        if module is None:
            continue

        built.append(module)
        taken.update(fn["name"] for fn in module.functions)
        on_event("ok", f"{spec['id']} verified"
                       + (f" after {module.attempts - 1} repair(s)" if module.attempts > 1 else ""))

    if not built:
        on_event("fail", "no module survived verification; nothing written")
        return report

    # Whole-lesson pass: catches anything only visible in combination.
    on_event("stage", "verifying the assembled lesson")
    combined = errors_only(verify_modules(curriculum, built))
    if combined:
        by_module = _attribute(combined, built)
        survivors = []
        for module in built:
            module_errors = by_module.get(module.id, [])
            if module_errors:
                others = [m for m in built if m.id != module.id]
                repaired = _repair_until_clean(
                    model, curriculum, module, taken - {f["name"] for f in module.functions},
                    grounding, report, on_event, accepted=others, extra=module_errors)
                if repaired is None:
                    continue
                survivors.append(repaired)
            else:
                survivors.append(module)
        built = survivors
        if not built:
            on_event("fail", "no module survived the assembled-lesson pass")
            return report

    destination = output_root / slug
    if destination.exists():
        shutil.rmtree(destination)
    write_lesson(destination, curriculum, built)

    final = errors_only(verify_lesson(slug, lessons_dir=output_root).items)
    if final:
        on_event("fail", f"assembled lesson still has {len(final)} error(s); "
                         "writing it anyway for inspection")
        for sev, where, msg in final[:5]:
            on_event("detail", f"{where}: {msg}")
        report.path = destination
        report.shipped = []
        report.dropped.extend([(f"lesson:{w}", [(s, w, m)]) for s, w, m in final])
        return report

    report.path = destination
    report.shipped = [m.id for m in built]
    report.cost_usd = getattr(model, "total_cost_usd", 0.0)
    return report


def _repair_until_clean(model: Model, curriculum: dict, module: BuiltModule,
                        taken: set[str], grounding: str, report: BuildReport,
                        on_event, accepted: list[BuiltModule] | None = None,
                        extra: list | None = None) -> BuiltModule | None:
    """Verify one module, repairing until it passes or the budget runs out."""
    accepted = accepted or []
    findings = extra if extra is not None else verify_candidate(curriculum, accepted, module)

    rounds = 0
    while findings and rounds < MAX_REPAIR_ROUNDS:
        rounds += 1
        report.repairs += 1
        on_event("repair", f"{module.id}: round {rounds}, {len(findings)} error(s), "
                           f"{findings[0][2][:90]}")
        try:
            module = run_repair(model, curriculum, module, findings, taken, grounding)
        except ModelAuthError:
            raise                 # a lost credential is not a defect in the module
        except ModelError as e:
            findings = [("ERROR", module.id, f"repair failed: {e}")]
            break
        findings = verify_candidate(curriculum, accepted, module)

    if findings:
        on_event("drop", f"{module.id}: still failing after {rounds} repair round(s)")
        module.findings = findings
        report.dropped.append((module.id, findings))
        return None
    return module


def _attribute(findings: list, modules: list[BuiltModule]) -> dict[str, list]:
    """Map whole-lesson findings back to the module that caused them."""
    ids = {m.id for m in modules}
    fn_owner = {fn["name"]: m.id for m in modules for fn in m.functions}
    out: dict[str, list] = {}
    for finding in findings:
        _, where, msg = finding
        owner = None
        head = where.split("/", 1)[0].split(" ", 1)[0]
        if head in ids:
            owner = head
        else:
            for name, mid in fn_owner.items():
                if name in msg or name in where:
                    owner = mid
                    break
        if owner:
            out.setdefault(owner, []).append(finding)
    return out
