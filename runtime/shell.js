// Sandbook shell: loads a lesson, boots the Python sandbox, mounts widgets.
//
// This file and everything it imports are hand-written and never generated.
// A generated lesson supplies only data (lesson.json) and pure functions
// (model.py); it cannot introduce code that runs on the main thread.

import { PySandbox } from "./py.js";
import { h } from "./render.js";

import * as paramPlayground from "./widgets/param-playground.js";
import * as predictReveal from "./widgets/predict-reveal.js";
import * as stepSim from "./widgets/step-sim.js";
import * as codeCell from "./widgets/code-cell.js";
import * as orderBuild from "./widgets/order-build.js";
import * as bugHunt from "./widgets/bug-hunt.js";
import * as paramHunt from "./widgets/param-hunt.js";
import * as calcWidget from "./widgets/calc-widget.js";
import * as diffApply from "./widgets/diff-apply.js";
import * as predictCurve from "./widgets/predict-curve.js";

const WIDGETS = {
  "param-playground": paramPlayground,
  "predict-reveal": predictReveal,
  "step-sim": stepSim,
  "code-cell": codeCell,
  "order-build": orderBuild,
  "bug-hunt": bugHunt,
  "param-hunt": paramHunt,
  "calc-widget": calcWidget,
  "diff-apply": diffApply,
  "predict-curve": predictCurve,
};

// Lessons live in ../lessons; freshly generated drafts live in ../output and
// are opened with ?from=output. Only these two roots are accepted, so a URL
// cannot point the loader at an arbitrary path.
const ROOTS = { lessons: "../lessons", output: "../output" };
const LESSON_ROOT = ROOTS[new URLSearchParams(location.search).get("from")] || ROOTS.lessons;
const main = document.getElementById("main");
const statusEl = document.getElementById("py-status");
const titleEl = document.getElementById("topbar-title");

let uidCounter = 0;

/* ------------------------------------------------------------ tiny markdown */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Escape first, then apply a deliberately small inline subset. */
function inline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

function renderProse(md) {
  const wrap = h("div", { class: "prose" });
  if (!md) return wrap;
  const blocks = String(md).trim().split(/\n\s*\n/);
  for (const block of blocks) {
    const lines = block.split("\n");
    const isBullet = lines.every((l) => /^\s*[-*]\s+/.test(l));
    const isNumber = lines.every((l) => /^\s*\d+[.)]\s+/.test(l));
    if (isBullet || isNumber) {
      const list = document.createElement(isBullet ? "ul" : "ol");
      for (const l of lines) {
        const li = document.createElement("li");
        li.innerHTML = inline(l.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ""));
        list.appendChild(li);
      }
      wrap.appendChild(list);
    } else {
      const p = document.createElement("p");
      p.innerHTML = inline(block.replace(/\n/g, " "));
      wrap.appendChild(p);
    }
  }
  return wrap;
}

/* ------------------------------------------------------------------ status */

function setStatus(state, text) {
  statusEl.dataset.state = state;
  statusEl.textContent = `python: ${text}`;
}

/* ----------------------------------------------------------------- library */

async function renderLibrary() {
  titleEl.textContent = "";
  statusEl.hidden = true;
  const lessons = [];
  for (const [where, root] of Object.entries(ROOTS)) {
    try {
      const res = await fetch(`${root}/index.json`, { cache: "no-store" });
      if (!res.ok) continue;
      for (const l of (await res.json()).lessons || []) lessons.push({ ...l, where });
    } catch { /* a missing index just means nothing built there yet */ }
  }

  main.replaceChildren();
  main.appendChild(h("div", { class: "lesson-head" }, [
    h("h1", { text: "Sandbook" }),
    h("p", { class: "subtitle", text: "Interactive lessons you can run, change, and test yourself." }),
  ]));

  if (!lessons.length) {
    main.appendChild(h("p", { class: "loading", text: "No lessons built yet." }));
    return;
  }
  const lib = h("div", { class: "library" });
  for (const l of lessons) {
    const query = `?lesson=${encodeURIComponent(l.slug)}`
      + (l.where === "output" ? "&from=output" : "");
    lib.appendChild(h("a", { class: "lesson-card", href: `./index.html${query}` }, [
      h("h3", {}, [
        l.title || l.slug,
        l.where === "output" ? h("span", { class: "draft-tag", text: "draft" }) : null,
      ]),
      h("p", { text: l.subtitle || "" }),
    ]));
  }
  main.appendChild(lib);
}

/* ------------------------------------------------------------------ lesson */

function widgetFrame(module) {
  const frame = h("div", { class: "widget" });
  const kind = module.widget.type;
  frame.appendChild(h("div", { class: "widget-head" }, [
    h("span", { class: "widget-kind", text: kind }),
    h("span", { class: "widget-title", text: module.widget.title || "" }),
  ]));
  const body = h("div", { class: "widget-body" });
  const errorSlot = h("div", {});
  frame.appendChild(body);
  frame.appendChild(errorSlot);
  return { frame, body, errorSlot };
}

async function renderLesson(slug) {
  const base = `${LESSON_ROOT}/${slug}`;
  const lesson = await (await fetch(`${base}/lesson.json`, { cache: "no-store" })).json();
  const modelPath = lesson.model || "model.py";
  const source = await (await fetch(`${base}/${modelPath}`, { cache: "no-store" })).text();

  document.title = `${lesson.title} · Sandbook`;
  titleEl.textContent = lesson.title || slug;

  const py = new PySandbox(setStatus);
  const ready = py.init(source, lesson.packages || []);
  ready.catch((e) => setStatus("error", e.message || "failed"));

  main.replaceChildren();
  main.appendChild(h("div", { class: "lesson-head" }, [
    h("h1", { text: lesson.title || slug }),
    lesson.subtitle ? h("p", { class: "subtitle", text: lesson.subtitle }) : null,
  ]));

  if ((lesson.objectives || []).length) {
    main.appendChild(h("div", { class: "objectives" }, [
      h("h2", { text: "By the end you should be able to" }),
      h("ul", {}, lesson.objectives.map((o) => h("li", { text: o }))),
    ]));
  }

  (lesson.modules || []).forEach((module, i) => {
    const section = h("section", { class: "module", id: module.id || `m${i + 1}` });
    section.appendChild(h("span", { class: "module-index", text: `${String(i + 1).padStart(2, "0")}` }));
    section.appendChild(h("h2", { text: module.title || "" }));
    if (module.prose) section.appendChild(renderProse(module.prose));

    if (module.widget) {
      const { frame, body, errorSlot } = widgetFrame(module);
      section.appendChild(frame);

      const impl = WIDGETS[module.widget.type];
      if (!impl) {
        errorSlot.replaceChildren(h("div", {
          class: "widget-error",
          text: `unknown widget type "${module.widget.type}": the runtime has no renderer for it`,
        }));
      } else {
        const ctx = {
          py,
          uid: `w${++uidCounter}`,
          whenReady(cb) { ready.then(cb).catch(() => {}); },
          showError(err) {
            const detail = err && err.trace ? `${err.message}\n\n${err.trace}` : (err && err.message) || String(err);
            errorSlot.replaceChildren(h("div", { class: "widget-error", text: detail }));
          },
          clearError() { errorSlot.replaceChildren(); },
        };
        try {
          impl.mount(body, module.widget, ctx);
        } catch (err) {
          ctx.showError(err);
        }
      }
    }
    main.appendChild(section);
  });

  if (lesson.sources && lesson.sources.length) {
    const foot = h("section", { class: "module" }, [h("h2", { text: "Sources" })]);
    foot.appendChild(h("ul", {}, lesson.sources.map((s) =>
      h("li", {}, [s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener", text: s.title || s.url }) : h("span", { text: s.title || "" })]))));
    main.appendChild(foot);
  }
}

/* -------------------------------------------------------------------- boot */

async function boot() {
  const slug = new URLSearchParams(location.search).get("lesson");
  try {
    if (slug) await renderLesson(slug);
    else await renderLibrary();
  } catch (err) {
    main.replaceChildren(h("div", { class: "widget-error", text: `Failed to load lesson: ${err.message}` }));
    setStatus("error", "load failed");
  }
}

boot();
