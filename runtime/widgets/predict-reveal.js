// predict-reveal: commit to a prediction before seeing the result.
//
// The correct option is never stated in the config. Each option carries a
// predicate over the value returned by `check.fn`; the runtime executes the
// function and the predicates to derive which option is right. The verifier
// runs the identical code path at build time and rejects the lesson unless
// exactly one predicate holds.
//
// config: {
//   question, options: [{id, text, predicate}], check: {fn, args},
//   view?: {fn, args}, explanation?
// }

import { resolveArgs } from "../bind.js";
import { renderView, h } from "../render.js";

export function mount(body, config, ctx) {
  const options = config.options || [];
  let picked = null;
  let revealed = false;

  body.appendChild(h("p", { class: "task-text", text: config.question || "" }));

  const list = h("div", { class: "options" });
  const buttons = options.map((opt, i) => {
    const btn = h("button", { class: "option", type: "button", "aria-pressed": "false" }, [
      h("span", { class: "tag", text: String.fromCharCode(97 + i) }),
      h("span", { text: opt.text || "" }),
    ]);
    btn.addEventListener("click", () => {
      if (revealed) return;
      picked = opt.id ?? String(i);
      buttons.forEach((b, j) => b.setAttribute("aria-pressed", String(j === i)));
      submit.disabled = false;
    });
    list.appendChild(btn);
    return btn;
  });
  body.appendChild(list);

  const submit = h("button", { class: "primary", type: "button", text: "Commit prediction" });
  submit.disabled = true;
  body.appendChild(h("div", { class: "button-row" }, [submit]));

  const verdictSlot = h("div", {});
  const reveal = h("div", { class: "reveal", hidden: "" });
  body.appendChild(verdictSlot);
  body.appendChild(reveal);

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      const args = resolveArgs(config.check.args, { params: {} });
      const predicates = options.map((o) => o.predicate);
      const { flags } = await ctx.py.check(config.check.fn, args, predicates);

      const trueIdx = flags.map((f, i) => (f ? i : -1)).filter((i) => i >= 0);
      if (trueIdx.length !== 1) {
        throw new Error(
          `contract violation: ${trueIdx.length} option predicates are true (expected exactly 1). ` +
          `This question cannot be scored and the lesson should not have shipped.`
        );
      }
      const correctIdx = trueIdx[0];
      const correctId = options[correctIdx].id ?? String(correctIdx);
      const right = picked === correctId;
      revealed = true;

      buttons.forEach((b, i) => {
        b.disabled = true;
        if (i === correctIdx) {
          b.classList.add("correct");
          b.appendChild(h("span", { class: "mark", text: "✓" }));
        } else if ((options[i].id ?? String(i)) === picked) {
          b.classList.add("incorrect");
          b.appendChild(h("span", { class: "mark", text: "✗" }));
        }
      });

      verdictSlot.replaceChildren(h("div", { class: `verdict ${right ? "right" : "wrong"}` }, [
        h("strong", { text: right ? "Correct." : "Not quite." }),
        h("span", { text: config.explanation || "" }),
      ]));

      if (config.view) {
        const view = await ctx.py.call(config.view.fn, resolveArgs(config.view.args, { params: {} }));
        reveal.replaceChildren(renderView(view));
        reveal.hidden = false;
      }
      submit.textContent = "Revealed";
      ctx.clearError();
    } catch (err) {
      submit.disabled = false;
      ctx.showError(err);
    }
  });

  return {};
}
