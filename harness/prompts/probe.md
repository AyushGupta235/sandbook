# Probe stage

Write one question per module to find out what this reader already understands,
before anything is built for them.

This is not a lesson and nothing here ships. Its only job is to decide which
modules are worth building, so a question that flatters the reader wastes their
money and a question that tricks them wastes their time.

## What a good probe question is

**It tests the misconception, not the vocabulary.** Someone who holds the wrong
belief must pick a wrong option, and someone who understands must pick the right
one. If a reader could get it right by recognising a term without understanding
the mechanism, the question tells you nothing.

**It is answerable from understanding alone.** No arithmetic that needs a
calculator, no recall of a specific default value, no trivia. You are probing a
mental model, not a memory.

**Its distractors are what people actually believe.** The best distractor is the
misconception stated confidently and plausibly, in the words someone who holds
it would use. A distractor nobody would pick is a wasted option and makes the
question easier than it should be.

**It has exactly one defensible answer.** If two options are arguably right,
the reader who picks the other one gets told they need a module they do not
need. Watch for options that are true but incomplete, or true under an
assumption you did not state.

## Calibrate the difficulty

Aim so that someone who genuinely knows the topic gets it right without
hesitating, and someone with the common misconception gets it wrong while
feeling confident. That second half is what makes the probe useful: a question
that merely feels hard is not the same as a question that separates.

## Output

Return one JSON object and nothing else.

```
{
  "questions": [
    {
      "module_id": "the module this probes, exactly as given",
      "question": "One or two sentences. Concrete, with any numbers stated inline.",
      "options": ["...", "...", "...", "..."],
      "answer_index": 0,
      "why": "One sentence a reader sees after answering: what is true and why the tempting option is not."
    }
  ]
}
```

Three or four options each. `answer_index` is zero-based. Write one question per
module listed below, in the same order, using the module's own `id`.

Never use an em-dash (`—`) or an en-dash (`–`) as punctuation.

## The lesson being planned

Title: {title}
Subtitle: {subtitle}

## The modules, and what each one is for

{modules}

{grounding}
