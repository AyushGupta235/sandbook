// code-cell: write something real and have it checked.
//
// Two modes, one contract.
//
//   Python mode  (config.tests)     : learner code is executed, then hidden
//                                     assertions run against it with the
//                                     lesson's model functions in scope.
//   Graded mode  (config.grade.fn)  : the learner's text is handed as a string
//                                     to a pure model function that parses and
//                                     judges it. This is what lets a lesson ask
//                                     for a Kubernetes manifest or a Terraform
//                                     block rather than only Python.
//
// The verifier enforces the same property in both modes: the reference solution
// passes AND the starter fails. Without the second half, a "fix this" exercise
// can silently ship already solved.
//
// config: { task, starter, solution, language?, hints?, tests? | grade: {fn} }

import { renderView, h } from "../render.js";

export function mount(body, config, ctx) {
  const graded = Boolean(config.grade && config.grade.fn);

  body.appendChild(h("p", { class: "task-text", text: config.task || "" }));

  const editor = h("textarea", {
    class: "editor", spellcheck: "false", autocapitalize: "off",
    autocorrect: "off", "aria-label": `${config.language || "python"} editor`,
  });
  editor.value = config.starter || "";
  body.appendChild(editor);

  // Tab indents instead of escaping the editor.
  editor.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const s = editor.selectionStart, en = editor.selectionEnd;
      const indent = config.language === "python" || !config.language ? "    " : "  ";
      editor.value = editor.value.slice(0, s) + indent + editor.value.slice(en);
      editor.selectionStart = editor.selectionEnd = s + indent.length;
    }
  });

  const runBtn = h("button", { class: "primary", type: "button", text: graded ? "Check" : "Run checks" });
  const resetBtn = h("button", { type: "button", text: "Reset" });
  const hintBtn = h("button", { type: "button", text: "Hint" });
  const solBtn = h("button", { type: "button", text: "Show solution" });
  const note = h("span", { class: "run-note", text: config.language ? `language: ${config.language}` : "" });

  const row = h("div", { class: "run-row" }, [runBtn, resetBtn]);
  if ((config.hints || []).length) row.appendChild(hintBtn);
  if (config.solution) row.appendChild(solBtn);
  row.appendChild(note);
  body.appendChild(row);

  const out = h("pre", { class: "console", hidden: "" });
  const checksSlot = h("div", {});
  const viewSlot = h("div", {});
  const hintBox = h("div", { class: "hints" });
  body.appendChild(out);
  body.appendChild(checksSlot);
  body.appendChild(viewSlot);
  body.appendChild(hintBox);

  let hintsShown = 0;
  let attempts = 0;

  hintBtn.addEventListener("click", () => {
    const hints = config.hints || [];
    if (hintsShown >= hints.length) return;
    hintBox.appendChild(h("div", { class: "hint", text: hints[hintsShown] }));
    hintsShown += 1;
    if (hintsShown >= hints.length) hintBtn.disabled = true;
  });

  function clearResults() {
    out.hidden = true;
    checksSlot.replaceChildren();
    viewSlot.replaceChildren();
  }

  resetBtn.addEventListener("click", () => {
    editor.value = config.starter || "";
    clearResults();
  });

  solBtn.addEventListener("click", () => {
    editor.value = config.solution;
    note.textContent = "solution loaded, run it to confirm";
  });

  function renderChecklist(details) {
    if (!Array.isArray(details) || !details.length) return;
    const list = h("div", { class: "checks" });
    for (const d of details) {
      list.appendChild(h("div", { class: `check ${d.ok ? "ok" : "bad"}` }, [
        h("span", { class: "check-mark", text: d.ok ? "✓" : "✗" }),
        h("span", {}, [
          h("span", { class: "check-label", text: d.label || "" }),
          // Notes explain failures. Showing one next to a green tick reads as a
          // contradiction, so a passing check stands on its own.
          !d.ok && d.note ? h("span", { class: "check-note", text: d.note }) : null,
        ]),
      ]));
    }
    checksSlot.replaceChildren(list);
  }

  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    note.textContent = "checking…";
    try {
      clearResults();
      attempts += 1;

      if (graded) {
        const res = await ctx.py.call(config.grade.fn, { submission: editor.value });
        const passed = Boolean(res && res.passed);
        out.hidden = false;
        out.className = `console ${passed ? "pass" : "fail"}`;
        out.textContent = (passed ? "✓ " : "✗ ") + (res.message || (passed ? "Looks right." : "Not there yet."));
        renderChecklist(res.details);
        if (res.view) viewSlot.replaceChildren(renderView(res.view));
        note.textContent = passed ? `passed in ${attempts} ${attempts === 1 ? "attempt" : "attempts"}` : "";
      } else {
        const res = await ctx.py.exec(editor.value, config.tests || "");
        const stdout = (res.stdout || "").trimEnd();
        out.hidden = false;
        if (res.ok) {
          out.className = "console pass";
          out.textContent = (stdout ? stdout + "\n\n" : "") + "✓ All checks passed.";
          note.textContent = `passed in ${attempts} ${attempts === 1 ? "attempt" : "attempts"}`;
        } else {
          out.className = "console fail";
          const where = res.stage === "code" ? "Your code raised an error" : "A check failed";
          out.textContent =
            (stdout ? stdout + "\n\n" : "") +
            `✗ ${where}:\n${res.error || "unknown error"}` +
            (res.trace ? `\n\n${res.trace}` : "");
          note.textContent = "";
        }
      }

      if (out.className.includes("fail") && attempts >= 2 && (config.hints || []).length && hintsShown === 0) {
        note.textContent = "stuck? try a hint";
      }
      ctx.clearError();
    } catch (err) {
      ctx.showError(err);
    } finally {
      runBtn.disabled = false;
    }
  });

  ctx.whenReady(() => {
    if (!note.textContent) note.textContent = config.language ? `language: ${config.language}` : "";
  });
  return {};
}
