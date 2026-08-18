# Repair stage

A module you built was rejected by the verifier. Rebuild it so the reported
defects are gone.

The verifier is not advisory. Every finding below is a hard failure, and the
module ships only when all of them are resolved.

## How to approach this

Read each finding and work out what is actually wrong before editing. Most
findings have a mechanical cause: a length mismatch, a name that does not
resolve, a predicate that does not hold, a simulation that never sets `done`.
Fix the cause rather than working around the symptom.

Two failures deserve particular care because they usually mean the content is
wrong rather than the wiring:

- **Predicate count.** If zero or several option predicates hold, you asserted
  numbers that the function does not produce. Compute the actual value, then
  rewrite the options around it. Do not loosen a predicate until one happens to
  match, which produces a question with a meaningless answer.
- **Starter passes / solution fails.** The exercise is not doing its job. The
  starter must be a plausible attempt that fails for the reason the module
  teaches, and the solution must genuinely pass.

Return the whole module, not a patch. Keep everything that was not at fault,
including prose and captions that were fine.

## Writing

Any text you rewrite follows the lesson's style rules. **Never use an em-dash
(`—`) or an en-dash (`–`) as punctuation**, in prose, captions, option text,
explanations, hints, assertion messages, or grader notes. Use a comma, a colon,
a semicolon, brackets, or two sentences. Say things directly, with no
throat-clearing and no summary paragraph restating the point.

If the module you were given contains one of those dashes, remove it while you
are in there, whether or not it was reported as a finding.

## Findings

{findings}

## The module as you last built it

{previous}

## Original brief

id: {module_id}
title: {module_title}
widget type: {widget_type}
intent: {intent}
teaching note: {teaching_note}

Function names used elsewhere in this lesson: {taken_names}

## Output

Return one JSON object in the same shape as before, and nothing else:
`{"prose": ..., "widget": ..., "functions": [...]}`
