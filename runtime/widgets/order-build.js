// order-build: put the steps in an order that actually works.
//
// The right order is never written in the config. `order.fn` returns both a
// canonical ordering and the constraints that make an ordering correct, and
// the widget judges the learner's sequence against those constraints. That
// matters because most real orderings are not unique: a dependency graph
// usually admits several valid topological orders, and a widget that accepts
// only one of them teaches the learner to guess which one the author wrote.
//
// The verifier checks the same function at build time: the canonical order
// must be a permutation of the items, it must satisfy every constraint it
// declares, and the order the items are listed in must violate at least one,
// so the exercise cannot ship already solved.
//
// config: {
//   task, items: [{id, label, detail?}],
//   order: {fn, args}, explanation?
// }
// order.fn returns {order: [id...], constraints: [[before, after], ...]}

import { resolveArgs } from "../bind.js";
import { h } from "../render.js";

export function mount(body, config, ctx) {
  const items = config.items || [];
  const byId = new Map(items.map((it, i) => [it.id ?? String(i), it]));
  let sequence = [];
  let solved = false;

  body.appendChild(h("p", { class: "task-text", text: config.task || "" }));

  const pool = h("div", { class: "order-pool" });
  const built = h("ol", { class: "order-built" });
  const empty = h("li", { class: "order-empty", text: "Click a step to add it here." });

  function redraw() {
    pool.replaceChildren(...items.map((it, i) => {
      const id = it.id ?? String(i);
      const used = sequence.includes(id);
      const chip = h("button", {
        class: `order-chip${used ? " used" : ""}`, type: "button",
      }, [
        h("span", { class: "order-label", text: it.label || id }),
        it.detail ? h("span", { class: "order-detail", text: it.detail }) : null,
      ]);
      // Set the property, never the attribute. h() stringifies whatever it is
      // given, so `disabled: false` would render disabled="false" and every
      // chip would be born unclickable.
      chip.disabled = used || solved;
      chip.addEventListener("click", () => {
        if (solved) return;
        sequence.push(id);
        redraw();
      });
      return chip;
    }));

    if (!sequence.length) {
      built.replaceChildren(empty);
    } else {
      built.replaceChildren(...sequence.map((id, pos) => {
        const it = byId.get(id) || {};
        const row = h("li", { class: "order-step" }, [
          h("span", { class: "order-label", text: it.label || id }),
          solved ? null : h("button", { class: "order-remove", type: "button", text: "remove" }),
        ]);
        if (!solved) {
          row.querySelector(".order-remove").addEventListener("click", () => {
            sequence.splice(pos, 1);
            redraw();
          });
        }
        return row;
      }));
    }
    submit.disabled = solved || sequence.length !== items.length;
    reset.disabled = solved || !sequence.length;
  }

  const submit = h("button", { class: "primary", type: "button", text: "Check the order" });
  const reset = h("button", { class: "secondary", type: "button", text: "Start over" });
  const verdict = h("div", {});

  body.appendChild(h("div", { class: "order-columns" }, [
    h("div", {}, [h("h4", { class: "order-heading", text: "Steps" }), pool]),
    h("div", {}, [h("h4", { class: "order-heading", text: "Your order" }), built]),
  ]));
  body.appendChild(h("div", { class: "button-row" }, [submit, reset]));
  body.appendChild(verdict);

  reset.addEventListener("click", () => {
    sequence = [];
    verdict.replaceChildren();
    redraw();
  });

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      const args = resolveArgs(config.order.args, { params: {} });
      const result = await ctx.py.call(config.order.fn, args);
      const constraints = result.constraints || [];

      const position = new Map(sequence.map((id, i) => [id, i]));
      const broken = constraints.filter(([before, after]) => {
        const a = position.get(before);
        const b = position.get(after);
        return a === undefined || b === undefined || a > b;
      });

      if (!broken.length) {
        solved = true;
        verdict.replaceChildren(h("div", { class: "verdict right" }, [
          h("strong", { text: "That order works." }),
          h("span", { text: config.explanation || "" }),
        ]));
      } else {
        // Name one broken dependency rather than all of them. Listing every
        // violation hands over the answer; one is enough to think with.
        const [before, after] = broken[0];
        const labelOf = (id) => (byId.get(id) || {}).label || id;
        verdict.replaceChildren(h("div", { class: "verdict wrong" }, [
          h("strong", { text: "Not yet." }),
          h("span", {
            text: `${labelOf(before)} has to come before ${labelOf(after)}`
              + (broken.length > 1 ? `, and ${broken.length - 1} other ordering(s) are wrong too.` : "."),
          }),
        ]));
      }
      redraw();
      ctx.clearError();
    } catch (err) {
      ctx.showError(err);
    } finally {
      if (!solved) submit.disabled = sequence.length !== items.length;
    }
  });

  redraw();
  return {};
}
