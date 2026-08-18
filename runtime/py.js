// Promise-based client for the Python sandbox worker.

export class PySandbox {
  constructor(onStatus) {
    this.worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
    this.pending = new Map();
    this.seq = 0;
    this.ready = null;
    this.onStatus = onStatus || (() => {});
    this.worker.onmessage = (e) => {
      const { id, progress } = e.data || {};
      if (progress) { this.onStatus("loading", progress); return; }
      const entry = this.pending.get(id);
      if (!entry) return;
      this.pending.delete(id);
      entry.resolve(e.data);
    };
    this.worker.onerror = (e) => {
      this.onStatus("error", e.message || "worker crashed");
      for (const [, entry] of this.pending) {
        entry.resolve({ ok: false, error: `python worker failed: ${e.message || "unknown"}` });
      }
      this.pending.clear();
    };
  }

  send(msg) {
    const id = ++this.seq;
    return new Promise((resolve) => {
      this.pending.set(id, { resolve });
      this.worker.postMessage({ ...msg, id });
    });
  }

  /** Load model source into the sandbox. Idempotent per sandbox instance. */
  init(source, packages) {
    if (!this.ready) {
      this.onStatus("loading", "starting python");
      this.ready = this.send({ type: "init", source, packages }).then((res) => {
        if (!res.ok) {
          this.onStatus("error", res.error || "init failed");
          throw new Error(res.error || "python init failed");
        }
        this.onStatus("ready", `ready · ${(res.names || []).length} fns`);
        this.names = res.names || [];
        return res;
      });
    }
    return this.ready;
  }

  /** Call a model function by name with keyword args. Returns the JSON result. */
  async call(fn, args) {
    await this.ready;
    const res = await this.send({ type: "call", fn, args });
    if (!res.ok) {
      const err = new Error(res.error || "python call failed");
      err.trace = res.trace;
      err.fn = fn;
      throw err;
    }
    return res.result;
  }

  /**
   * Derive an answer: call `fn`, then evaluate each option predicate against
   * the result. Returns { result, flags } where flags[i] is that option's truth.
   */
  async check(fn, args, predicates) {
    await this.ready;
    const res = await this.send({ type: "check", fn, args, predicates });
    if (!res.ok) {
      const err = new Error(res.error || "python check failed");
      err.trace = res.trace;
      throw err;
    }
    return res;
  }

  /** Run learner code followed by hidden test source. Never throws. */
  async exec(code, tests) {
    await this.ready;
    return this.send({ type: "exec", code, tests });
  }
}
