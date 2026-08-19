// calc-widget: work the number out yourself first.
//
// The expected value is never in the config. `answer.fn` computes it, and the
// learner's entry is compared against what the function returns, within a
// stated tolerance. So the number the lesson calls correct is the number its
// own model produces, and cannot drift away from it.
//
// config: {
//   task, prompt?, unit?, tolerance?, answer: {fn, args},
//   working?: {fn, args}, hints?: [...], explanation?
// }

import { resolveArgs, formatValue } from "../bind.js";
import { renderView, h } from "../render.js";

export function mount(body, config, ctx) {
  let solved = false;
  let attempts = 0;

  body.appendChild(h("p", { class: "task-text", text: config.task || "" }));

  const input = h("input", {
    class: "calc-input", type: "text", inputmode: "decimal",
    placeholder: config.placeholder || "your answer",
    "aria-label": config.prompt || "answer",
  });
  const unit = config.unit ? h("span", { class: "calc-unit", text: config.unit }) : null;
  const submit = h("button", { class: "primary", type: "button", text: "Check" });

  body.appendChild(h("div", { class: "calc-row" }, [
    config.prompt ? h("label", { class: "calc-prompt", text: config.prompt }) : null,
    h("div", { class: "calc-entry" }, [input, unit]),
    submit,
  ]));

  const verdict = h("div", {});
  const working = h("div", { class: "reveal", hidden: "" });
  body.appendChild(verdict);
  body.appendChild(working);

  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit.click(); });

  submit.addEventListener("click", async () => {
    if (solved) return;
    const entered = Number(String(input.value).replace(/[, _]/g, ""));
    if (!input.value.trim() || !Number.isFinite(entered)) {
      verdict.replaceChildren(h("div", { class: "verdict wrong" }, [
        h("strong", { text: "That is not a number." }),
        h("span", { text: "Enter a plain figure, for example 1250 or 3.5." }),
      ]));
      return;
    }

    submit.disabled = true;
    try {
      const expected = await ctx.py.call(config.answer.fn, resolveArgs(config.answer.args, { params: {} }));
      if (typeof expected !== "number" || !Number.isFinite(expected)) {
        throw new Error(`contract violation: ${config.answer.fn} returned ${JSON.stringify(expected)}, `
          + "which is not a finite number, so nothing can be marked right or wrong.");
      }
      // Relative tolerance once the numbers get large, absolute near zero.
      const tol = config.tolerance ?? 1e-6;
      const slack = Math.abs(tol) + Math.abs(tol * expected);
      const right = Math.abs(entered - expected) <= slack;
      attempts += 1;

      if (right) {
        solved = true;
        input.disabled = true;
        submit.textContent = "Correct";
        verdict.replaceChildren(h("div", { class: "verdict right" }, [
          h("strong", { text: `${formatValue(expected, config.format)}${config.unit || ""}. Correct.` }),
          h("span", { text: config.explanation || "" }),
        ]));
        if (config.working) {
          const view = await ctx.py.call(config.working.fn, resolveArgs(config.working.args, { params: {} }));
          working.replaceChildren(renderView(view));
          working.hidden = false;
        }
      } else {
        const hints = config.hints || [];
        const hint = hints.length ? hints[Math.min(attempts - 1, hints.length - 1)] : "";
        verdict.replaceChildren(h("div", { class: "verdict wrong" }, [
          h("strong", {
            text: Math.abs(entered) > Math.abs(expected) ? "Too high." : "Too low.",
          }),
          h("span", { text: hint || "Work through the units again." }),
        ]));
      }
      ctx.clearError();
    } catch (err) {
      ctx.showError(err);
    } finally {
      if (!solved) submit.disabled = false;
    }
  });

  return {};
}
