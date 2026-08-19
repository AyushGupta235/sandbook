// diff-apply: choose the change that actually fixes it.
//
// Same contract as bug-hunt, different question. bug-hunt asks *where* the
// defect is; diff-apply asks *which repair works*, which is the harder skill
// once you already know roughly where the problem lives. Several changes look
// reasonable and only one holds up.
//
// Each candidate replaces the whole listing, so a fix may touch several lines.
// The tests decide, at build time and again in the browser.
//
// config: {
//   task, code, tests, candidates: [{id, label, detail?, code}], explanation?
// }

import { h } from "../render.js";

export function mount(body, config, ctx) {
  const candidates = config.candidates || [];
  let picked = null;
  let solved = false;
  const tried = new Set();

  body.appendChild(h("p", { class: "task-text", text: config.task || "" }));

  body.appendChild(h("div", { class: "diff-current" }, [
    h("h4", { class: "order-heading", text: "As it stands" }),
    h("pre", { class: "diff-code" }, [h("code", { text: config.code || "" })]),
  ]));

  const list = h("div", { class: "options" });
  const buttons = candidates.map((c, i) => {
    const btn = h("button", { class: "option", type: "button", "aria-pressed": "false" }, [
      h("span", { class: "tag", text: String.fromCharCode(97 + i) }),
      h("span", {}, [
        h("span", { text: c.label || c.id }),
        c.detail ? h("span", { class: "order-detail", text: c.detail }) : null,
      ]),
    ]);
    btn.addEventListener("click", () => {
      if (solved) return;
      picked = c;
      buttons.forEach((b, j) => b.setAttribute("aria-pressed", String(j === i)));
      submit.disabled = false;
    });
    list.appendChild(btn);
    return btn;
  });
  body.appendChild(list);

  const submit = h("button", { class: "primary", type: "button", text: "Apply this change" });
  submit.disabled = true;
  body.appendChild(h("div", { class: "button-row" }, [submit]));

  const verdict = h("div", {});
  const output = h("pre", { class: "bug-output", hidden: "" });
  body.appendChild(verdict);
  body.appendChild(output);

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    const chosen = picked;
    try {
      const res = await ctx.py.exec(chosen.code, config.tests || "");
      tried.add(chosen.id);

      output.textContent = (res.stdout || "").trim()
        || (res.error ? `${res.stage === "tests" ? "tests failed" : "error"}: ${res.error}` : "");
      output.hidden = !output.textContent;

      const idx = candidates.indexOf(chosen);
      if (res.ok) {
        solved = true;
        buttons.forEach((b, i) => {
          b.disabled = true;
          if (i === idx) b.classList.add("correct");
        });
        verdict.replaceChildren(h("div", { class: "verdict right" }, [
          h("strong", { text: "That one holds up." }),
          h("span", { text: config.explanation || "" }),
        ]));
      } else {
        buttons[idx].classList.add("incorrect");
        buttons[idx].disabled = true;
        picked = null;
        buttons.forEach((b) => b.setAttribute("aria-pressed", "false"));
        const left = candidates.length - tried.size;
        verdict.replaceChildren(h("div", { class: "verdict wrong" }, [
          h("strong", { text: "That change does not survive the tests." }),
          h("span", {
            text: left > 0
              ? "Read what failed before picking again."
              : "Every option has now failed, which means the lesson shipped broken.",
          }),
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
