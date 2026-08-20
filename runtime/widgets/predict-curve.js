// predict-curve: draw the shape before you see it.
//
// The visual counterpart of predict-reveal, and a harder commitment. Multiple
// choice lets you recognise an answer; a curve has to be produced. Someone who
// thinks rollout time falls linearly with surge will draw a straight line, and
// there is no option list to nudge them off it.
//
// Only the *shape* is judged. Both curves are normalised to [0, 1] before
// comparison, because the learner cannot know the absolute scale and being
// wrong about the units is not the misconception under test.
//
// The verifier requires the true curve to differ from a straight line between
// its endpoints by more than the tolerance. Without that a learner could drag
// nothing, leave the default flat line, and pass.
//
// config: {
//   task, points?, tolerance?, x_label?, y_label?, unit?,
//   curve: {fn, args}, explanation?
// }
// curve.fn returns {x: [...], y: [...]}

import { resolveArgs } from "../bind.js";
import { h } from "../render.js";

const SVG = "http://www.w3.org/2000/svg";
const W = 560, H = 300, M = { l: 52, r: 18, t: 18, b: 44 };

function el(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) node.setAttribute(k, String(v));
  }
  if (text !== undefined) node.textContent = String(text);
  return node;
}

/** Scale to [0, 1]. A flat curve maps to all zeros rather than dividing by 0. */
export function normalise(values) {
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo;
  return span === 0 ? values.map(() => 0) : values.map((v) => (v - lo) / span);
}

/** Mean absolute difference between two normalised shapes. */
export function shapeDistance(a, b) {
  const n = Math.min(a.length, b.length);
  if (!n) return Infinity;
  let total = 0;
  for (let i = 0; i < n; i++) total += Math.abs(a[i] - b[i]);
  return total / n;
}

export function mount(body, config, ctx) {
  const count = Math.max(4, Math.min(12, config.points || 7));
  const tolerance = config.tolerance ?? 0.12;
  const plotW = W - M.l - M.r, plotH = H - M.t - M.b;
  // Start flat and mid-height: any other default would suggest an answer.
  let drawn = Array.from({ length: count }, () => 0.5);
  let solved = false;
  let dragging = false;

  body.appendChild(h("p", { class: "task-text", text: config.task || "" }));

  const root = el("svg", {
    viewBox: `0 0 ${W} ${H}`, class: "chart curve-chart",
    style: `max-width: ${W}px`, role: "application",
    "aria-label": config.task || "draw the curve you expect",
  });

  const gridLayer = el("g");
  const truthLayer = el("g");
  const drawnLayer = el("g");
  root.append(gridLayer, truthLayer, drawnLayer);

  for (let i = 0; i <= 4; i++) {
    const y = M.t + (plotH * i) / 4;
    gridLayer.appendChild(el("line", { class: "grid-line", x1: M.l, x2: W - M.r, y1: y, y2: y }));
  }
  gridLayer.appendChild(el("line", { class: "axis-line", x1: M.l, x2: M.l, y1: M.t, y2: M.t + plotH }));
  gridLayer.appendChild(el("line", {
    class: "axis-line", x1: M.l, x2: W - M.r, y1: M.t + plotH, y2: M.t + plotH,
  }));
  if (config.x_label) {
    gridLayer.appendChild(el("text", {
      class: "axis-label", x: M.l + plotW / 2, y: H - 10, "text-anchor": "middle",
    }, config.x_label));
  }
  if (config.y_label) {
    gridLayer.appendChild(el("text", {
      class: "axis-label", x: 14, y: M.t + plotH / 2, "text-anchor": "middle",
      transform: `rotate(-90 14 ${M.t + plotH / 2})`,
    }, config.y_label));
  }

  const px = (i) => M.l + (plotW * i) / (count - 1);
  const py = (v) => M.t + plotH - v * plotH;

  function redraw() {
    const pts = drawn.map((v, i) => `${px(i)},${py(v)}`).join(" ");
    drawnLayer.replaceChildren(
      el("polyline", { points: pts, class: "curve-drawn" }),
      ...drawn.map((v, i) => el("circle", {
        cx: px(i), cy: py(v), r: solved ? 3 : 6, class: "curve-handle",
      })),
    );
  }

  function setFromEvent(event) {
    const box = root.getBoundingClientRect();
    if (!box.width) return;
    const sx = (event.clientX - box.left) * (W / box.width);
    const sy = (event.clientY - box.top) * (H / box.height);
    const i = Math.round(((sx - M.l) / plotW) * (count - 1));
    if (i < 0 || i >= count) return;
    drawn[i] = Math.max(0, Math.min(1, (M.t + plotH - sy) / plotH));
    redraw();
    submit.disabled = false;
  }

  root.addEventListener("pointerdown", (e) => {
    if (solved) return;
    dragging = true;
    root.setPointerCapture(e.pointerId);
    setFromEvent(e);
  });
  root.addEventListener("pointermove", (e) => { if (dragging && !solved) setFromEvent(e); });
  root.addEventListener("pointerup", (e) => {
    dragging = false;
    if (root.hasPointerCapture(e.pointerId)) root.releasePointerCapture(e.pointerId);
  });

  body.appendChild(root);

  const submit = h("button", { class: "primary", type: "button", text: "That is my prediction" });
  const reset = h("button", { class: "secondary", type: "button", text: "Start over" });
  body.appendChild(h("div", { class: "button-row" }, [submit, reset]));
  const verdict = h("div", {});
  body.appendChild(verdict);

  reset.addEventListener("click", () => {
    if (solved) return;
    drawn = Array.from({ length: count }, () => 0.5);
    verdict.replaceChildren();
    redraw();
  });

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      const truth = await ctx.py.call(config.curve.fn, resolveArgs(config.curve.args, { params: {} }));
      const ys = (truth.y || []).map(Number);
      if (ys.length < 2 || ys.some((v) => !Number.isFinite(v))) {
        throw new Error(`contract violation: ${config.curve.fn} returned no usable curve`);
      }
      // Resample the true curve onto the learner's control points.
      const sampled = drawn.map((_, i) => {
        const at = (i / (count - 1)) * (ys.length - 1);
        const lo = Math.floor(at), hi = Math.ceil(at);
        return ys[lo] + (ys[hi] - ys[lo]) * (at - lo);
      });
      const target = normalise(sampled);
      const distance = shapeDistance(normalise(drawn), target);
      const close = distance <= tolerance;
      solved = true;

      truthLayer.replaceChildren(el("polyline", {
        points: target.map((v, i) => `${px(i)},${py(v)}`).join(" "),
        class: "curve-truth",
      }));
      redraw();

      verdict.replaceChildren(h("div", { class: `verdict ${close ? "right" : "wrong"}` }, [
        h("strong", {
          text: close ? "Close enough: that is the shape."
                      : "Not that shape. The real one is drawn over yours.",
        }),
        h("span", { text: config.explanation || "" }),
      ]));
      reset.disabled = true;
      submit.textContent = "Revealed";
      ctx.clearError();
    } catch (err) {
      submit.disabled = false;
      ctx.showError(err);
    }
  });

  redraw();
  return {};
}
