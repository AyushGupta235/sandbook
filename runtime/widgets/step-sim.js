// step-sim: walk an algorithm forward one state at a time.
//
// config: {
//   init: {fn, args}, step: {fn, args}, view: {fn, args},
//   max_steps?, autoplay_ms?
// }
//
// The state is whatever `init.fn` returns (must be JSON). `step.fn` receives it
// via a {"state": true} binding and returns the next state. A state carrying
// "done": true ends the run.

import { resolveArgs } from "../bind.js";
import { renderView, h } from "../render.js";

export function mount(body, config, ctx) {
  const maxSteps = config.max_steps ?? 32;
  let state = null;
  let stepCount = 0;
  let timer = null;

  const counter = h("span", { text: "step 0" });
  const meta = h("div", { class: "step-meta" }, [counter]);
  const viewSlot = h("div", {});

  const stepBtn = h("button", { class: "primary", type: "button", text: "Step ▸" });
  const playBtn = h("button", { type: "button", text: "Run ▸▸" });
  const resetBtn = h("button", { type: "button", text: "Reset" });
  const row = h("div", { class: "button-row" }, [stepBtn, playBtn, resetBtn]);

  body.appendChild(meta);
  body.appendChild(viewSlot);
  body.appendChild(row);

  const scope = () => ({ params: {}, state });

  function done() {
    return Boolean(state && state.done) || stepCount >= maxSteps;
  }

  async function draw() {
    const view = await ctx.py.call(config.view.fn, resolveArgs(config.view.args, scope()));
    viewSlot.replaceChildren(renderView(view));
    counter.textContent = `step ${stepCount}${done() ? " · complete" : ""}`;
    stepBtn.disabled = done();
    playBtn.disabled = done();
  }

  async function reset() {
    stopPlay();
    try {
      state = await ctx.py.call(config.init.fn, resolveArgs(config.init.args, { params: {} }));
      stepCount = 0;
      await draw();
      ctx.clearError();
    } catch (err) {
      ctx.showError(err);
    }
  }

  async function step() {
    if (done()) return;
    try {
      state = await ctx.py.call(config.step.fn, resolveArgs(config.step.args, scope()));
      stepCount += 1;
      await draw();
      ctx.clearError();
    } catch (err) {
      stopPlay();
      ctx.showError(err);
    }
  }

  function stopPlay() {
    if (timer) { clearInterval(timer); timer = null; playBtn.textContent = "Run ▸▸"; }
  }

  stepBtn.addEventListener("click", step);
  resetBtn.addEventListener("click", reset);
  playBtn.addEventListener("click", () => {
    if (timer) { stopPlay(); return; }
    playBtn.textContent = "Pause ❚❚";
    timer = setInterval(async () => {
      if (done()) { stopPlay(); return; }
      await step();
    }, config.autoplay_ms ?? 650);
  });

  ctx.whenReady(reset);
  return { reset, step };
}
