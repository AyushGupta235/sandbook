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

### `order-build`

Fields: `task`, `items` (each with `id`, `label`, optional `detail`), `order.fn`.
The learner arranges the steps. `order.fn` returns
`{order: [id...], constraints: [[before, after], ...]}`, and the arrangement is
judged against the **constraints**, so every order that would really work is
accepted. Constrain only what is genuinely required: two steps that happen
together should be left unordered, or a learner who is right gets told they are
wrong.

Enforced: at least three items with unique ids, at least one constraint, `order`
is an arrangement of exactly those ids and satisfies every constraint, and the
order the items are *listed* in breaks at least one, so it cannot be solved by
clicking top to bottom.

### `param-hunt`

Fields: `task`, `params` (as `param-playground`), `goal.fn`, optional `view.fn`.
The learner has to *achieve* something rather than observe. `goal.fn` returns
`{met, message, detail?}`, and the message is the teaching surface: say which
requirement fails and by how much.

Enforced: the goal is **unmet at the defaults** and met somewhere in the space,
so the exercise ships neither already solved nor impossible.

### `calc-widget`

Fields: `task`, `answer.fn`, optional `prompt`, `unit`, `tolerance`, `format`,
`working.fn`, `hints`. The learner works a number out by hand. The expected value
is computed, never stored, so it cannot drift from the model. `tolerance` is
absolute near zero and relative for large values; use `0` for exact integers.
Hints are consumed in order across attempts, and `working` is revealed only once
the answer is right.

### `bug-hunt`

Fields: `task`, `code`, `tests`, `candidates` (each `id`, `line`, `patch`,
optional `label`). The learner picks the wrong line; that line's patch is applied
and the tests run, so the answer is demonstrated rather than announced. `line` is
1-indexed and a patch replaces exactly that line.

Enforced: `code` as shipped **fails** the tests and **exactly one** candidate's
patch makes them pass. Give wrong candidates a patch identical to the line they
replace. Note that this only works if the tests pin more than the one line;
otherwise a compensating fix elsewhere also passes and the exercise is ambiguous.

### `diff-apply`

Fields: `task`, `code`, `tests`, `candidates` (each `id`, `label`, `code`,
optional `detail`). Same contract as `bug-hunt`, different question: not where
the defect is but which repair holds up. Each candidate carries the whole listing
as it would be after the change, so a fix may span several lines. Make the wrong
candidates things a reasonable reviewer would suggest.

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

`./sandbook selftest` runs the kernels' property tests, the note-grounding
tests, 42 planted defects across three reference lessons, and the pipeline
regression against recorded live builds. When you add a check, add the mutation
that proves it fires.

5. **Claims.** A function declaring `implements: <kernel>` is run against the
   trusted implementation in `kernels/` over that kernel's probe inputs and must
   agree with it.
6. **Provenance.** A citation without a followable url is an error. A lesson
   pinning a tool version warns if it cites nothing, records no date, or is over
   a year old.

## Known gap

The contract proves a lesson is well-formed, executable, and self-consistent,
which a confidently wrong lesson also is. Two things narrow that gap: kernels
settle the claims a lesson stakes against a known primitive, and `--review` has
a fresh context check the rest against what the code actually returns. The
review is a model judging a model, so it cannot be proven correct the way the
kernel check can, and it is opt-in for that reason.

What nothing here checks is whether the *choice* of what to teach is any good.
