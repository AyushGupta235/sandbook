# The lesson contract

This is the entire interface between a lesson and the runtime. Everything outside
it is hand-written harness code that generation never touches.

A lesson is a directory under `lessons/<slug>/`:

```
lessons/<slug>/
├── lesson.json   # curriculum and widget configuration (data only)
└── model.py      # pure functions, the mechanics of the concept
```

The generated surface is deliberately narrow: data and pure functions. No HTML,
no CSS, no JavaScript, no DOM access, no rendering code. That is what makes the
output checkable rather than merely plausible.

## Rules for `model.py`

1. **Pure.** No I/O, no network, no global mutation, no unseeded randomness.
2. **JSON in, JSON out.** Arguments and return values are plain lists, dicts,
   numbers, strings, and booleans. NaN and infinity are rejected at the boundary,
   so a numeric blowup fails loudly instead of rendering as an empty chart.
3. **Deterministic.** The verifier calls these functions in CPython and the
   browser calls them in Pyodide through
   [`runtime/sandbox_bootstrap.py`](../runtime/sandbox_bootstrap.py). One shared
   file, so the two environments cannot drift apart.
4. Functions ending in `_view` return a view object, never drawing code.

## View objects

A view is data describing what to draw. The renderer is hand-written and tested
once. A lesson supplies only the numbers.

| kind | required fields | notes |
|---|---|---|
| `bars` | `labels`, `values` | `len(labels)` must equal `len(values)` |
| `lines` | `x`, `series[].values` | every series must match `len(x)` |
| `grid` | `cells` | row and column label counts must match |
| `scalars` | `items[].value` | readouts and summary numbers |
| `text` | `text` | prose emitted by a function |
| `stack` | `panels` | composes any of the above |

Optional on most kinds: `caption`, `x_label`, `y_label`, `y_min`, `y_max`,
`highlight`, `value_format`.

## Argument bindings

Widget configs name a function and bind its keyword arguments:

```jsonc
{"fn": "dist_view", "args": {
  "preset":      {"param": "preset"},   // current value of a control
  "temperature": {"param": "temperature"},
  "top_p":       {"const": 0.9},        // fixed value
  "state":       {"state": true}        // a step-sim's current state
}}
```

## Widgets

### `param-playground`

Controls bound to a view function, redrawn live. Optional `readouts` render
scalar summaries. Verified by calling the view function at the default settings
and at every boundary of every control.

### `predict-reveal`

The learner commits to a prediction before seeing the answer. The config never
states which option is correct. Each option carries a `predicate` evaluated
against the value returned by `check.fn`, and both the runtime and the verifier
derive correctness by executing it. A lesson is rejected unless exactly one
predicate holds. Asserting an answer key through a `correct` or `answer` field is
itself an error.

### `step-sim`

`init.fn` produces a state, `step.fn` advances it, `view.fn` draws it. A state
carrying `done: true` ends the run. Rejected if it fails to terminate within
`max_steps`.

### `code-cell`

Fields: `task`, `starter`, `solution`, optional `hints` and `language`. Two
modes, and a lesson must pick exactly one.

**Python mode** (`tests`) executes the learner's code, then runs hidden
assertions against it with the model's functions in scope, so a check can compare
the learner's work against a trusted implementation.

**Graded mode** (`grade.fn`) passes the learner's text as a string to a pure
model function that parses and judges it. This is how a lesson asks for a
Kubernetes manifest, a Terraform block, or any other non-Python artefact. The
submission is data and is never executed. The grader returns
`{passed, message, details: [{label, ok, note}], view?}`, and notes are shown only
for checks that failed.

Both modes enforce the same two properties: the solution passes and the starter
fails. Without the second, an exercise can ship already solved. A graded
rejection must also point at a specific failing check, so the learner is never
told "not there yet" with nothing to act on.

## What the verifier enforces

Run `./sandbook verify`. Any single error blocks the lesson.

1. **Structure.** Known widget types, sane control ranges, defaults in range.
2. **References.** Every function and parameter a widget names exists.
3. **Execution.** Every view renders at every reachable corner of its parameter
   space and satisfies its view-kind contract.
4. **Contract.** The rules above: derived answers, exercises that are solvable
   but not already solved, simulations that terminate.

Lessons declaring `packages` (loaded by Pyodide in the browser) are checked
against the verifier's own Python first, so an environment mismatch fails with a
clear message instead of an ImportError from inside a model.

`./sandbook selftest` plants 17 known defects across both reference lessons and
confirms each is caught. When you add a check, add the mutation that proves it
fires.

## Known gap

The verifier proves a lesson is well-formed, executable, and self-consistent. It
does not prove the content is true. A confident, wrong explanation with matching
code would pass today. Closing that needs trusted reference implementations for
well-known primitives plus an independent review pass against cited sources.
Until then, treat generated lessons as drafts rather than references.
