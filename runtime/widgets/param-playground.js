// param-playground: bind controls to a model function and redraw its view live.
//
// config: { params: [...], view: {fn, args}, readouts?: [{label, fn, args, format, unit}] }

import { resolveArgs, formatValue } from "../bind.js";
import { renderView, h } from "../render.js";

export function mount(body, config, ctx) {
  const params = config.params || [];
  const state = { params: {} };
  for (const p of params) state.params[p.id] = p.default;

  const controls = h("div", { class: "params" });
  const valueEls = {};

  for (const p of params) {
    const row = h("div", { class: "param" });
    row.appendChild(h("label", { for: `p-${ctx.uid}-${p.id}`, text: p.label || p.id }));

    if (p.kind === "choice") {
      const sel = h("select", { id: `p-${ctx.uid}-${p.id}` });
      for (const opt of p.options || []) {
        const o = h("option", { value: JSON.stringify(opt.value), text: opt.label ?? String(opt.value) });
        if (JSON.stringify(opt.value) === JSON.stringify(p.default)) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener("change", () => {
        state.params[p.id] = JSON.parse(sel.value);
        refresh();
      });
      row.appendChild(sel);
    } else {
      const input = h("input", {
        type: "range", id: `p-${ctx.uid}-${p.id}`,
        min: p.min, max: p.max, step: p.step ?? 1, value: p.default,
      });
      const out = h("span", { class: "value", text: formatValue(p.default, p.format) });
      valueEls[p.id] = { el: out, fmt: p.format, unit: p.unit };
      input.addEventListener("input", () => {
        state.params[p.id] = Number(input.value);
        out.textContent = formatValue(Number(input.value), p.format) + (p.unit ? ` ${p.unit}` : "");
        refresh();
      });
      out.textContent = formatValue(p.default, p.format) + (p.unit ? ` ${p.unit}` : "");
      row.appendChild(input);
      row.appendChild(out);
    }
    controls.appendChild(row);
  }

  const viewSlot = h("div", {});
  const readoutSlot = h("div", {});
  body.appendChild(controls);
  body.appendChild(viewSlot);
  body.appendChild(readoutSlot);

  let generation = 0;
  async function refresh() {
    const mine = ++generation;
    try {
      const args = resolveArgs(config.view.args, state);
      const view = await ctx.py.call(config.view.fn, args);
      if (mine !== generation) return; // a newer slider event won
      viewSlot.replaceChildren(renderView(view));

      if (config.readouts && config.readouts.length) {
        const items = [];
        for (const r of config.readouts) {
          const value = await ctx.py.call(r.fn, resolveArgs(r.args, state));
          items.push({ label: r.label, value, format: r.format, unit: r.unit });
        }
        if (mine !== generation) return;
        readoutSlot.replaceChildren(renderView({ kind: "scalars", items }));
      }
      ctx.clearError();
    } catch (err) {
      if (mine === generation) ctx.showError(err);
    }
  }

  ctx.whenReady(refresh);
  return { refresh };
}
