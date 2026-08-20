# Revise stage

A reader has looked at a lesson outline and told you what they want changed.
Return a revised outline that does what they asked and still holds together.

## What they asked for is the point

Take the instructions literally. If they say they already understand something,
that module goes; do not keep it "briefly" because you think it is important.
They know what they know, and the outline is theirs.

If they ask for something added, work out where it belongs in the sequence and
which widget suits it, rather than appending it to the end. A topic bolted on
after the summary is worse than one that was never added.

## Where judgement is actually required

**Removing a module can strand the ones after it.** Modules lean on each other:
drop the one that introduces a mental model and the next three may lose their
footing. When that happens you have two honest options, and one dishonest one.

- Fold what is still needed into a module they kept, so the idea survives
  without the module they rejected. Usually right.
- Keep the sequence and let a later module carry the weight itself.
- **Not this:** quietly reinstating what they cut under a new title.

When you fold something in, say so in `revision_note`. A reader who cut a module
and finds it back under another name will stop trusting the outline, and they
would be right to.

**Renumbering.** After changes, module ids should still read as a sequence a
person would write. Keep the ids of modules that survived unchanged, so the
reader can see what stayed.

## Keep the shape

The revised outline follows the same rules as any other: 3 to 8 modules, each
with one widget, each targeting a specific misconception, and a `teaching_note`
concrete enough for a builder to follow literally.

If the instructions would leave fewer than three modules, say so in
`revision_note` and return what remains anyway. It is their lesson.

## Output

Return one JSON object and nothing else, in the same shape as a curriculum,
plus one extra field:

```
{
  "slug": "...", "title": "...", "subtitle": "...",
  "targets": "... or null",
  "sources": [{"title": "...", "url": "...", "version": "... or null"}],
  "packages": [],
  "objectives": ["..."],
  "misconceptions": [{"claim": "...", "reality": "..."}],
  "modules": [
    {"id": "...", "title": "...", "widget_type": "...",
     "intent": "...", "teaching_note": "...", "misconception": "..."}
  ],
  "revision_note": "What you changed and why, in two or three sentences. Name anything you folded into another module, and anything you could not do."
}
```

Never use an em-dash (`—`) or an en-dash (`–`) as punctuation.

Available widget types and kernels are unchanged:

{kernels}

## The outline as it stands

{previous}

## What the reader asked for

{instructions}

{grounding}
