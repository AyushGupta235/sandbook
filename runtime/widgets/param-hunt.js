// param-hunt: find a setting that satisfies a requirement.
//
// A param-playground shows what happens. A param-hunt asks the learner to make
// something specific happen, which is a different and harder thing: they have
// to reason about which direction to move, not just watch a curve.
//
// Whether the goal is met is decided by `goal.fn`, never by the config. The
// verifier requires that the goal is *not* met at the default settings and
// *is* met somewhere in the space, so the exercise ships neither already solved
// nor impossible.
//
// config: {
//   task, params: [...], goal: {fn, args}, view?: {fn, args}, explanation?
// }
// goal.fn returns {met: bool, message: str, detail?: str}

import { resolveArgs } from "../bind.js";
import { renderView, h } from "../render.js";

export function mount(body, config, ctx) {
  const params = config.params || [];
  const values = {};
  let solved = false;

  body.appendChild(h("p", { class: "task-text", text: config.task || "" }));

  const controls = h("div", { class: "params" });
  const readouts = new Map();

  for (const p of params) {
    values[p.id] = p.default;
    const valueEl = h("output", { class: "param-value" });
    readouts.set(p.id, valueEl);

    let control;
    if (p.kind === "choice") {
      control = h("select", { class: "param-select" });
      for (const opt of p.options || []) {
        const o = h("option", { value: String(opt.value), text: opt.label || String(opt.value) });
        if (opt.value === p.default) o.selected = true;
        control.appendChild(o);
      }
      control.addEventListener("change", () => {
        values[p.id] = control.value;
        refresh();
      });
    } else {
      control = h("input", {
        class: "param-range", type: "range",
        min: String(p.min), max: String(p.max), step: String(p.step ?? 1),
        value: String(p.default),
      });
      control.addEventListener("input", () => {
        values[p.id] = Number(control.value);
        refresh();
      });
    }

    controls.appendChild(h("div", { class: "param" }, [
      h("label", { class: "param-label", text: p.label || p.id }),
      control,
      valueEl,
    ]));
  }
  body.appendChild(controls);

  const status = h("div", { class: "hunt-status" });
  const viewSlot = h("div", {});
  body.appendChild(status);
  body.appendChild(viewSlot);

  function paintReadouts() {
    for (const p of params) {
      const el = readouts.get(p.id);
      if (!el) continue;
      const v = values[p.id];
      el.textContent = p.kind === "choice"
        ? String(v)
        : `${typeof v === "number" ? v : ""}${p.unit || ""}`;
    }
  }

  let pending = 0;
  async function refresh() {
    paintReadouts();
    const seq = ++pending;
    try {
      const result = await ctx.py.call(config.goal.fn, resolveArgs(config.goal.args, { params: values }));
      if (seq !== pending) return;    // a newer drag already superseded this
      if (!result || typeof result.met !== "boolean") {
        throw new Error(`contract violation: ${config.goal.fn} must return {met, message}`);
      }

      status.className = `hunt-status ${result.met ? "met" : "unmet"}`;
      status.replaceChildren(
        h("strong", { text: result.met ? "Goal met." : "Not there yet." }),
        h("span", { text: result.message || "" }),
        result.detail ? h("span", { class: "hunt-detail", text: result.detail }) : null,
      );

      if (result.met && !solved) {
        solved = true;
        if (config.explanation) {
          status.appendChild(h("p", { class: "hunt-explain", text: config.explanation }));
        }
      }

      if (config.view) {
        const view = await ctx.py.call(config.view.fn, resolveArgs(config.view.args, { params: values }));
        if (seq !== pending) return;
        viewSlot.replaceChildren(renderView(view));
      }
      ctx.clearError();
    } catch (err) {
      ctx.showError(err);
    }
  }

  ctx.whenReady(refresh);
  paintReadouts();
  return {};
}
