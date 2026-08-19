// bug-hunt: find the line that is actually wrong.
//
// Which line is wrong is never stated. Each candidate line carries a patch,
// and correctness is settled by applying that patch and running the hidden
// tests: the right line is the one whose fix makes them pass. The learner sees
// the same thing happen, so the answer is demonstrated rather than announced.
//
// The verifier runs the identical procedure at build time and requires the
// code as shipped to fail the tests, and exactly one candidate's patch to fix
// them. A bug-hunt where two different lines both "work", or where the code was
// never broken, cannot ship.
//
// config: {
//   task, code, tests, candidates: [{id, line, label?, patch}], explanation?
// }

import { h } from "../render.js";

/** Replace one 1-indexed line, which is the only edit a candidate may make. */
export function applyPatch(code, line, patch) {
  const lines = code.split("\n");
  if (line < 1 || line > lines.length) {
    throw new Error(`candidate points at line ${line}, but the listing has ${lines.length}`);
  }
  lines[line - 1] = patch;
  return lines.join("\n");
}

export function mount(body, config, ctx) {
  const candidates = config.candidates || [];
  const byLine = new Map(candidates.map((c) => [c.line, c]));
  let picked = null;
  let solved = false;

  body.appendChild(h("p", { class: "task-text", text: config.task || "" }));

  const listing = h("div", { class: "bug-listing" });
  const rows = config.code.split("\n").map((text, i) => {
    const lineNo = i + 1;
    const candidate = byLine.get(lineNo);
    const row = h(candidate ? "button" : "div", {
      class: `bug-line${candidate ? " selectable" : ""}`,
      ...(candidate ? { type: "button" } : {}),
    }, [
      h("span", { class: "bug-lineno", text: String(lineNo) }),
      h("code", { class: "bug-code", text: text || " " }),
    ]);
    if (candidate) {
      row.addEventListener("click", () => {
        if (solved) return;
        picked = candidate;
        rows.forEach((r) => r.classList.remove("picked"));
        row.classList.add("picked");
        submit.disabled = false;
      });
    }
    listing.appendChild(row);
    return row;
  });
  body.appendChild(listing);

  const submit = h("button", { class: "primary", type: "button", text: "This line is wrong" });
  submit.disabled = true;
  body.appendChild(h("div", { class: "button-row" }, [submit]));

  const verdict = h("div", {});
  const output = h("pre", { class: "bug-output", hidden: "" });
  body.appendChild(verdict);
  body.appendChild(output);

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      const patched = applyPatch(config.code, picked.line, picked.patch);
      const res = await ctx.py.exec(patched, config.tests || "");

      output.textContent = (res.stdout || "").trim()
        || (res.error ? `${res.stage === "tests" ? "tests failed" : "error"}: ${res.error}` : "");
      output.hidden = !output.textContent;

      if (res.ok) {
        solved = true;
        rows.forEach((r) => { r.disabled = true; });
        verdict.replaceChildren(h("div", { class: "verdict right" }, [
          h("strong", { text: "That was the bug." }),
          h("span", { text: config.explanation || "" }),
        ]));
      } else {
        rows.forEach((r) => r.classList.remove("picked"));
        picked = null;
        verdict.replaceChildren(h("div", { class: "verdict wrong" }, [
          h("strong", { text: "Fixing that line does not fix the behaviour." }),
          h("span", { text: "The tests still fail. Read what they report and try another line." }),
        ]));
      }
      ctx.clearError();
    } catch (err) {
      ctx.showError(err);
    } finally {
      if (!solved) submit.disabled = picked === null;
    }
  });

  return {};
}
