// View renderer.
//
// Generated model functions never draw anything. They return a plain "view"
// object and this hand-written module turns it into DOM. That keeps all
// rendering code inside the harness where it can be tested once, and reduces
// what a model has to get right to a small, schema-checkable data shape.
//
// Supported kinds: bars | lines | grid | scalars | text | stack

import { formatValue } from "./bind.js";

const SVG = "http://www.w3.org/2000/svg";
const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4"];

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) node.setAttribute(k, String(v));
  }
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function h(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = String(v);
    else if (v !== undefined && v !== null) node.setAttribute(k, String(v));
  }
  for (const c of [].concat(children)) {
    if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

/** Round a domain out to readable tick values. */
function niceTicks(min, max, count = 5) {
  if (!isFinite(min) || !isFinite(max) || min === max) {
    const base = isFinite(max) && max !== 0 ? max : 1;
    return { lo: Math.min(0, base), hi: Math.max(0, base), ticks: [Math.min(0, base), Math.max(0, base)] };
  }
  const raw = (max - min) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let t = lo; t <= hi + step * 1e-9; t += step) ticks.push(Math.round(t / step) * step);
  return { lo, hi, ticks };
}

function tickLabel(v) {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e6 || a < 1e-3) return v.toExponential(1).replace("e+", "e");
  if (Number.isInteger(v)) return String(v);
  return String(Math.round(v * 1000) / 1000);
}

/* ------------------------------------------------------------------ bars */

function renderBars(view) {
  const values = (view.values || []).map(Number);
  const labels = view.labels || values.map((_, i) => String(i));
  const highlight = new Set(view.highlight || []);

  const W = 720, H = 300;
  const M = { l: 56, r: 16, t: 26, b: 54 };
  const plotW = W - M.l - M.r;
  const plotH = H - M.t - M.b;

  const dataMax = values.length ? Math.max(...values) : 1;
  const dataMin = values.length ? Math.min(...values) : 0;
  const wantMax = view.y_max !== undefined && view.y_max !== null ? Number(view.y_max) : dataMax * 1.15 || 1;
  const wantMin = view.y_min !== undefined && view.y_min !== null ? Number(view.y_min) : Math.min(0, dataMin * 1.15);
  const { lo, hi, ticks } = niceTicks(wantMin, wantMax, 5);
  const span = hi - lo || 1;
  const y = (v) => M.t + plotH - ((v - lo) / span) * plotH;

  const root = svg("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`,
    preserveAspectRatio: "xMidYMid meet", role: "img",
    "aria-label": view.aria_label || view.y_label || "bar chart",
  });

  for (const t of ticks) {
    root.appendChild(svg("line", { class: "grid-line", x1: M.l, x2: W - M.r, y1: y(t), y2: y(t) }));
    root.appendChild(svg("text", {
      class: "tick-text", x: M.l - 9, y: y(t) + 4, "text-anchor": "end",
    }, tickLabel(t)));
  }

  root.appendChild(svg("line", { class: "axis-line", x1: M.l, x2: M.l, y1: M.t, y2: M.t + plotH }));
  root.appendChild(svg("line", { class: "axis-line", x1: M.l, x2: W - M.r, y1: y(Math.max(lo, 0)), y2: y(Math.max(lo, 0)) }));

  const n = values.length || 1;
  const slot = plotW / n;
  const barW = Math.max(6, Math.min(72, slot * 0.66));
  const zero = y(Math.max(lo, Math.min(0, hi)));

  values.forEach((v, i) => {
    const cx = M.l + slot * (i + 0.5);
    const top = Math.min(y(v), zero);
    const height = Math.max(1, Math.abs(zero - y(v)));
    root.appendChild(svg("rect", {
      class: highlight.has(i) ? "bar-rect hl" : "bar-rect",
      x: cx - barW / 2, y: top, width: barW, height, rx: 3,
    }));
    if (view.show_values !== false && n <= 16) {
      root.appendChild(svg("text", {
        class: "bar-value", x: cx, y: top - 7,
      }, formatValue(v, view.value_format || ".3f")));
    }
    if (n <= 20) {
      root.appendChild(svg("text", { class: "cat-label", x: cx, y: M.t + plotH + 19 }, labels[i] ?? ""));
    }
  });

  if (view.x_label) {
    root.appendChild(svg("text", { class: "axis-label", x: M.l + plotW / 2, y: H - 8, "text-anchor": "middle" }, view.x_label));
  }
  if (view.y_label) {
    root.appendChild(svg("text", {
      class: "axis-label", x: 14, y: M.t + plotH / 2, "text-anchor": "middle",
      transform: `rotate(-90 14 ${M.t + plotH / 2})`,
    }, view.y_label));
  }
  return root;
}

/* ----------------------------------------------------------------- lines */

function renderLines(view) {
  const series = view.series || [];
  const xs = (view.x || []).map(Number);
  const W = 720, H = 320;
  const M = { l: 60, r: 18, t: 22, b: 52 };
  const plotW = W - M.l - M.r;
  const plotH = H - M.t - M.b;

  const all = series.flatMap((s) => (s.values || []).map(Number)).filter(Number.isFinite);
  const { lo, hi, ticks } = niceTicks(
    view.y_min !== undefined && view.y_min !== null ? Number(view.y_min) : Math.min(...all, 0),
    view.y_max !== undefined && view.y_max !== null ? Number(view.y_max) : Math.max(...all, 0),
    5
  );
  const xLo = xs.length ? Math.min(...xs) : 0;
  const xHi = xs.length ? Math.max(...xs) : 1;
  const xSpan = xHi - xLo || 1;
  const ySpan = hi - lo || 1;
  const px = (v) => M.l + ((v - xLo) / xSpan) * plotW;
  const py = (v) => M.t + plotH - ((v - lo) / ySpan) * plotH;

  const root = svg("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`,
    preserveAspectRatio: "xMidYMid meet", role: "img",
    "aria-label": view.aria_label || "line chart",
  });

  for (const t of ticks) {
    root.appendChild(svg("line", { class: "grid-line", x1: M.l, x2: W - M.r, y1: py(t), y2: py(t) }));
    root.appendChild(svg("text", { class: "tick-text", x: M.l - 9, y: py(t) + 4, "text-anchor": "end" }, tickLabel(t)));
  }
  const xTickCount = Math.min(6, xs.length);
  for (let i = 0; i < xTickCount; i++) {
    const v = xLo + (xSpan * i) / Math.max(1, xTickCount - 1);
    root.appendChild(svg("text", { class: "tick-text", x: px(v), y: M.t + plotH + 20, "text-anchor": "middle" }, tickLabel(v)));
  }
  root.appendChild(svg("line", { class: "axis-line", x1: M.l, x2: M.l, y1: M.t, y2: M.t + plotH }));
  root.appendChild(svg("line", { class: "axis-line", x1: M.l, x2: W - M.r, y1: M.t + plotH, y2: M.t + plotH }));

  series.forEach((s, si) => {
    const color = `var(${SERIES_VARS[si % SERIES_VARS.length]})`;
    const pts = (s.values || [])
      .map((v, i) => (Number.isFinite(Number(v)) && xs[i] !== undefined ? `${px(xs[i])},${py(Number(v))}` : null))
      .filter(Boolean)
      .join(" ");
    root.appendChild(svg("polyline", {
      points: pts, fill: "none", stroke: color, "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round",
    }));
  });

  if (view.x_label) {
    root.appendChild(svg("text", { class: "axis-label", x: M.l + plotW / 2, y: H - 8, "text-anchor": "middle" }, view.x_label));
  }
  if (view.y_label) {
    root.appendChild(svg("text", {
      class: "axis-label", x: 14, y: M.t + plotH / 2, "text-anchor": "middle",
      transform: `rotate(-90 14 ${M.t + plotH / 2})`,
    }, view.y_label));
  }

  const wrap = h("div", {}, [root]);
  if (series.length > 1) {
    const legend = h("div", { class: "legend" });
    series.forEach((s, si) => {
      const sw = h("span", { class: "legend-swatch" });
      sw.style.background = `var(${SERIES_VARS[si % SERIES_VARS.length]})`;
      legend.appendChild(h("span", { class: "legend-item" }, [sw, s.label || `series ${si + 1}`]));
    });
    wrap.appendChild(legend);
  }
  return wrap;
}

/* ------------------------------------------------------------------ grid */

function renderGrid(view) {
  const cells = view.cells || [];
  const colLabels = view.col_labels || [];
  const rowLabels = view.row_labels || [];
  const table = h("table", { class: "grid-table" });

  if (colLabels.length) {
    const tr = h("tr");
    tr.appendChild(h("th", {}));
    for (const c of colLabels) tr.appendChild(h("th", { text: c }));
    table.appendChild(tr);
  }
  cells.forEach((row, ri) => {
    const tr = h("tr");
    tr.appendChild(h("th", { class: "row-head", text: rowLabels[ri] ?? String(ri) }));
    (row || []).forEach((cell) => {
      const obj = cell && typeof cell === "object" ? cell : { v: cell };
      const state = obj.state || (obj.v === null || obj.v === undefined || obj.v === "" ? "empty" : "filled");
      const label = obj.label !== undefined && obj.label !== null
        ? String(obj.label)
        : obj.v === null || obj.v === undefined ? "·" : formatValue(obj.v, view.value_format);
      tr.appendChild(h("td", { class: `cell-${state}`, text: label, title: obj.title || "" }));
    });
    table.appendChild(tr);
  });

  return h("div", { class: "view-scroll" }, [table]);
}

/* --------------------------------------------------------------- scalars */

function renderScalars(view) {
  const wrap = h("div", { class: "scalars" });
  for (const item of view.items || []) {
    const box = h("div", { class: "scalar" });
    box.appendChild(h("span", { class: "k", text: item.label ?? "" }));
    const v = h("span", { class: "v", text: formatValue(item.value, item.format) });
    if (item.unit) v.appendChild(h("span", { class: "u", text: item.unit }));
    box.appendChild(v);
    wrap.appendChild(box);
  }
  return wrap;
}

/* ------------------------------------------------------------------ main */

export function renderView(view) {
  if (!view || typeof view !== "object") {
    return h("div", { class: "widget-error", text: `expected a view object, got ${JSON.stringify(view)}` });
  }
  const wrap = h("div", { class: "view" });
  let body;
  switch (view.kind) {
    case "bars": body = renderBars(view); break;
    case "lines": body = renderLines(view); break;
    case "grid": body = renderGrid(view); break;
    case "scalars": body = renderScalars(view); break;
    case "text": body = h("p", { class: "prose", text: view.text ?? "" }); break;
    case "stack":
      body = h("div", {}, (view.panels || []).map(renderView));
      break;
    default:
      return h("div", { class: "widget-error", text: `unknown view kind: ${JSON.stringify(view.kind)}` });
  }
  wrap.appendChild(body);
  if (view.caption) wrap.appendChild(h("p", { class: "view-caption", text: view.caption }));
  return wrap;
}

export { h };
