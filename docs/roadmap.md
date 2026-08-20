# Roadmap: richer lessons, chosen depth, curriculum you steer

Written 2026-08-20, after M0 through M4 landed. Three things to fix, in the
order they are worth doing.

The constraint that shapes all of this: **whatever gets added has to stay
checkable.** Every widget so far derives its answer by running code rather than
storing it, and that is the only reason a generated lesson can be trusted. A
feature that cannot be verified is not a feature here, it is a liability, so
each section below states what the verifier would check.

---

## Part 1: The curriculum you actually want (M5)

**Do this first.** It is the highest-value change and the cheapest to build,
because it reorganises stages that already exist rather than adding new ones.

### The problem

`sandbook build` is one shot: topic in, five modules out, no say in what they
are. If two of them cover things you already know, you paid for them, you read
past them, and the lesson is worse for including them. Worse, the modules you
*needed* were crowded out by a fixed 4-to-6 budget.

### Split planning from building

```bash
sandbook plan "Kubernetes admission control"     # ~$0.50, grounding + curriculum only
sandbook plan --edit <slug>                      # revise the outline
sandbook build --from-plan <slug>                # the expensive part, on an outline you approve
```

`plan` writes `output/<slug>/curriculum.json` and prints the outline: each
module's title, widget type, intent, and **the misconception it targets**. That
last field is the one worth reading, because it is what decides whether a module
earns its place.

Editing works two ways, and both matter:

- **The file.** `$EDITOR output/<slug>/curriculum.json`. Delete a module, add
  one, reorder. No new interface to learn.
- **A prompt loop.** `plan --edit` walks module by module: keep, drop, *"I know
  this"*, *"go deeper here"*, or add a topic in your own words.

### The part that is not obvious: dropping a module is not deleting a line

Modules build on each other. Drop the one that introduces the mental model and
the next three lose their footing, and nothing in a text editor will tell you
that. So after edits, a **revise** stage runs one cheap call: it gets the
original outline, your keep/drop/add list, and the grounding, and returns a
re-balanced outline that is coherent again. It may reintroduce a concept you cut
by folding it into a module you kept, and it should say so rather than silently
restoring it.

Verifier work: the revised outline is schema-checked like any curriculum, module
ids stay unique, and every module still declares a targeted misconception.

---

## Part 2: Stop asking what you know, and find out (M6)

**The strongest idea here, and it depends on Part 1.**

`plan --edit` asks you to self-assess. Self-assessment is exactly the signal
this project should not trust: the whole premise is that a confident wrong
belief is worse than an absent one, and someone holding one will tell you they
know the topic. They are not lying, they are the person the lesson is for.

So test instead.

```bash
sandbook plan "KV-caching" --probe
```

Before building, generate five `predict-reveal` questions, one per misconception
the outline targets. You answer. Then:

- **Handled correctly** → the module is dropped, or compressed to a single
  paragraph that states the conclusion without belabouring it
- **Missed** → the module is kept and expanded, and the question you got wrong
  becomes its opening
- **Missed badly** (picked the option that implies a deeper confusion) → a
  prerequisite module is inserted

This reuses machinery that already exists. Probe questions are `predict-reveal`
widgets under the existing contract: the answer is derived by executing a
predicate, exactly one must hold, and the verifier already refuses anything
else. A probe is a three-minute lesson that happens to decide what the real
lesson contains.

### Making it compound

`~/.sandbook/profile.json` records which misconceptions you have demonstrably
handled, with dates. Later builds skip what is established, and say what they
skipped.

Two ways that goes wrong, and the mitigations:

- **Knowledge decays.** Anything older than six months is re-probed rather than
  assumed. The record is evidence with a timestamp, not a permanent claim.
- **A wrong "you know this" silently removes content**, which is the same class
  of harm as a wrong lesson. So the profile never silently drops a module: every
  build prints what it skipped and why, and `--no-profile` ignores it entirely.

---

## Part 3: Visualisations that fit the subject (M7)

Six view kinds exist: `bars`, `lines`, `grid`, `scalars`, `text`, `stack`. They
are all essentially charts, and several topics have been squeezed into a `grid`
because nothing better existed. Three new kinds, ranked by how many lessons
need them.

### `graph` — the biggest gap

Nodes and edges with automatic layout. Dependency structure is the *subject* of
half the infra library and there is currently no way to draw it: the Terraform
lesson renders a dependency graph as a table of rows.

```
{"kind": "graph",
 "nodes": [{"id": "vpc", "label": "aws_vpc.main", "group": "network"}],
 "edges": [["vpc", "subnet"]],
 "highlight": ["subnet"], "layout": "layered"}
```

Layered layout by longest-path depth, which the `dag_order` kernel already
computes, so the renderer and the kernel agree by construction.

Pairs with: `step-sim` (watch apply walk the graph, one resource at a time),
`order-build` (order the steps with the graph in front of you), `param-hunt`
(change a dependency and watch the critical path move).

Verifier: every edge references a declared node, no duplicate ids, the layout
terminates, and a graph declaring itself acyclic actually is.

### `timeline` — lanes and spans

Rollout waves, retry schedules with backoff, Temporal event history, request
lifecycle, scheduling over time. All currently faked with grids.

```
{"kind": "timeline", "unit": "s",
 "lanes": [{"label": "pod-1", "spans": [{"start": 0, "end": 15, "label": "starting", "state": "pending"}]}]}
```

Pairs with: `param-playground` (drag `maxSurge`, watch the schedule compress),
`step-sim` (advance one event at a time), `calc-widget` (predict total duration,
then see the timeline that produces it).

Verifier: spans have `end >= start`, lanes are labelled, no span escapes the
declared window.

### `heatmap` — a continuous scale over a matrix

`grid` shows discrete cells; attention matrices, cache access patterns and
utilisation maps need a continuous colour scale with a legend and a stated
domain. Largely an extension of `grid` rather than a new renderer.

### One renderer feature worth more than a new kind

**Change highlighting in `step-sim`.** Every step currently redraws from
scratch, so the learner has to diff two frames by eye. Marking what changed
since the previous state, and briefly holding that mark, is a small renderer
change that makes every existing step-sim easier to follow.

### One new widget: `predict-curve`

The visual analogue of `predict-reveal`, and the highest-engagement idea here.
The learner is shown axes and asked to place or sketch the shape *before* seeing
it: how does KV-cache size grow with context length, how does throughput scale
with batch, what does latency do as utilisation approaches one.

Committing to a shape exposes a misconception that multiple choice lets you
dodge, because you cannot pattern-match your way to a curve.

Verified the same way as everything else: the true curve comes from a model
function, and the learner's points are compared within a tolerance. The check
that makes it honest is a new one worth having: **the misconception shape must
differ from the true shape by more than the tolerance**, or the question cannot
be scored and the lesson is rejected.

---

## Part 4: Depth as a stated level (M8)

Cheapest of the four, deliberately last: it is polish on top of a curriculum you
can already edit, and Part 1 solves most of the same problem more directly.

```bash
sandbook build "KV-caching" --level deep
sandbook build "Helm" --assume "I use Kubernetes daily, never written a chart"
```

Named levels, not a number, because a number invites the model to pad:

| level | modules | assumes | targets |
|---|---|---|---|
| `orientation` | 3 | nothing | what it is, why it exists, one hands-on |
| `working` | 5 (default) | competent engineer, new to this | correct use and the common failure |
| `deep` | 7-8 | has used it | mechanism, edge cases, behaviour under load |
| `expert` | 5-6 | fluent | only the counterintuitive parts |

The important part: **depth changes which misconceptions are targeted, not just
how many modules there are.** An `expert` KV-caching lesson should not explain
what a cache is; it should go at paged attention, prefix sharing, and
quantisation tradeoffs. A level that only changes module count is a padding
knob, and padding is what makes lessons worth skipping.

`--assume` is the free-text escape hatch and is probably used more than the
levels, since it says the one thing a level cannot.

---

## Sequencing, and why

1. **M5, plan/edit/build split.** Highest value, lowest effort, no new
   verification. Everything else is better once you can steer the outline.
2. **M6, probe and profile.** Depends on M5. Turns curriculum design from
   self-report into evidence, and compounds across lessons.
3. **M7, `graph` and `timeline` and `predict-curve`.** Most visible improvement
   and the most renderer work. Independent of M5 and M6, so it can run in
   parallel or slot in whenever.
4. **M8, levels.** Cheapest, and partly redundant once M5 exists.

## What is deliberately not on this list

- **A GUI curriculum editor.** A terminal prompt and a JSON file are enough, and
  a web editor is a large surface to maintain for a small gain.
- **A general animation framework.** Targeted change-highlighting in `step-sim`
  earns its keep; a tweening layer does not.
- **Free-form numeric depth (`--modules 12`).** Invites padding.
- **Letting generated code touch the renderer.** The reason any of this is
  verifiable is that lessons emit data and never drawing code. Every view kind
  above keeps that line intact.
