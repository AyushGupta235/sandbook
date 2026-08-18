// Sandbook Python sandbox. Runs generated model code and learner code inside a
// web worker, off the main thread. Everything crossing the boundary is JSON, so
// generated functions can never hand the UI a live object.
//
// The Python side lives in ./sandbox_bootstrap.py, shared verbatim with the
// verifier's subprocess runner so both execute lessons under identical rules.

const PYODIDE_VERSION = "314.0.5";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

let pyodide = null;
const fns = { load: null, call: null, exec: null, check: null };

async function boot(packages) {
  const bootstrapURL = new URL("./sandbox_bootstrap.py", import.meta.url);
  const bootstrapSrc = await (await fetch(bootstrapURL)).text();

  const { loadPyodide } = await import(PYODIDE_URL + "pyodide.mjs");
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  if (packages && packages.length) await pyodide.loadPackage(packages);

  pyodide.runPython(bootstrapSrc);
  fns.load = pyodide.globals.get("_sb_load");
  fns.call = pyodide.globals.get("_sb_call");
  fns.check = pyodide.globals.get("_sb_check");
  fns.exec = pyodide.globals.get("_sb_exec");
}

self.onmessage = async (event) => {
  const msg = event.data || {};
  const { id, type } = msg;
  try {
    if (type === "init") {
      if (!pyodide) {
        self.postMessage({ id, progress: "downloading runtime" });
        await boot(msg.packages);
      }
      self.postMessage({ id, ok: true, ...JSON.parse(fns.load(msg.source)) });
      return;
    }
    if (!pyodide) throw new Error("python sandbox is not initialised yet");

    if (type === "call") {
      self.postMessage({ id, ...JSON.parse(fns.call(msg.fn, JSON.stringify(msg.args ?? {}))) });
      return;
    }
    if (type === "check") {
      self.postMessage({
        id,
        ...JSON.parse(fns.check(msg.fn, JSON.stringify(msg.args ?? {}), JSON.stringify(msg.predicates ?? []))),
      });
      return;
    }
    if (type === "exec") {
      self.postMessage({ id, ...JSON.parse(fns.exec(msg.code, msg.tests ?? "")) });
      return;
    }
    throw new Error(`unknown message type: ${type}`);
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};
