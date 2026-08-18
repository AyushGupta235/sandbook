# sandbook

Turns a technical topic into an interactive lesson that runs on your machine:
sliders wired to real computations, step-through simulations, questions that make
you commit to a prediction before you see the answer, and code you write and run
in the browser.

The target is topics where no good video or interactive tool exists. On the ML
side, things like KV-caching, distillation, and post-training RL. On the
infrastructure side, Terraform graphs, Helm templating, Kubernetes scheduling,
and Temporal workflow replay.

## Approach

This is not a general-purpose app generator pointed at education. It is a narrow
pipeline with an output contract that can be checked mechanically.

A generated lesson supplies exactly two things: `lesson.json` (data) and
`model.py` (pure functions). The runtime shell, the widgets, and all rendering
code are hand-written and never generated. Keeping the generated surface that
small is what makes verification possible instead of hoping the output came out
right.

Three rules do most of the work.

1. **Quiz answers are computed, not written down.** Each option carries a
   predicate over the value a model function returns, and correctness is derived
   by executing it. A lesson that ships its own answer key is rejected.
2. **Exercises must be solvable and not already solved.** The reference solution
   has to pass its checks, and the starter code has to fail them.
3. **Model functions are pure and JSON-only.** The verifier runs them in CPython
   and the browser runs them in Pyodide through one shared file, so the two
   environments cannot drift apart.

## Status

Runtime, widget library, sandbox, verifier, CLI, and two hand-written reference
lessons, working end to end. Lesson generation is the next piece. Until then
lessons are written by hand and held to the same verifier.

## Try it

```bash
./sandbook serve
```

Then open <http://localhost:8765/runtime/index.html>.

```bash
./sandbook list        # built lessons
./sandbook verify      # hold every lesson to the contract
./sandbook selftest    # check that the verifier still catches planted defects
```

Learner code runs in Pyodide inside a web worker. The verifier runs the same code
in a subprocess through the same bootstrap. Nothing is installed globally, and
the only network request is the Pyodide download from jsDelivr.

## Layout

| Path | Role |
|---|---|
| `runtime/` | Shell, widgets, SVG renderer. Hand-written, never generated. |
| `runtime/sandbox_bootstrap.py` | Sandbox semantics shared by browser and verifier. |
| `lessons/<slug>/` | A lesson: `lesson.json` plus `model.py`. |
| `verifier/` | Contract checks, subprocess runner, mutation suite. |
| `harness/` | CLI. The generation pipeline lands here next. |

## On trusting the output

The verifier checks that a lesson is well-formed, executable, and internally
consistent. It does not yet check that the content is true. A confident, wrong
explanation with matching code would pass today. Closing that gap needs trusted
reference implementations for known primitives plus a separate review pass
against cited sources, which is planned but not built.

`./sandbook selftest` is how the verifier earns trust: it plants 17 known defects
across both lessons and confirms each one is caught. A verifier that has never
rejected anything is not evidence of much. Every new check should ship with the
mutation that proves it fires.

## Reading order

1. [`docs/lesson-format.md`](docs/lesson-format.md), the authoring contract
2. [`lessons/softmax-and-temperature/`](lessons/softmax-and-temperature/), a worked example
3. [`verifier/test_mutations.py`](verifier/test_mutations.py), what verification actually buys you

## Prior art

[OpenMAIC](https://github.com/thu-maic/openmaic) turns a topic into an AI-led
classroom with teacher and classmate agents. Closest in spirit, but it is a large
multi-agent chat system and its output is a conversation rather than a tool you
manipulate. [E2B Fragments](https://github.com/e2b-dev/fragments) is an
open-source Artifacts clone: general-purpose, no pedagogy, no verification. The
best interactive explainers, such as
[transformer-explainer](https://github.com/poloclub/transformer-explainer) and
PhET, are hand-authored, and they set the quality bar here.
