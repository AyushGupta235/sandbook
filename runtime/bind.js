// Argument binding: how a widget config names the inputs of a model function.
//
//   {"const": 3}          → literal value
//   {"param": "temp"}     → current value of the widget's control
//   {"state": true}       → the step-sim's current state object
//
// Anything else is passed through as a literal, so plain JSON works too.

export function resolveBinding(spec, scope) {
  if (spec && typeof spec === "object" && !Array.isArray(spec)) {
    if ("const" in spec) return spec.const;
    if ("param" in spec) {
      if (!(spec.param in scope.params)) {
        throw new Error(`binding references unknown param "${spec.param}"`);
      }
      return scope.params[spec.param];
    }
    if ("state" in spec) return scope.state;
  }
  return spec;
}

export function resolveArgs(spec, scope) {
  const out = {};
  for (const [key, binding] of Object.entries(spec || {})) {
    out[key] = resolveBinding(binding, scope);
  }
  return out;
}

/** Format a number the way a config asks for, e.g. ".3f" / "d" / "%". */
export function formatValue(value, fmt) {
  if (value === null || value === undefined) return "-";
  if (typeof value !== "number") return String(value);
  if (!fmt) return trimNumber(value);
  if (fmt === "d") return Math.round(value).toLocaleString();
  if (fmt === "%") return `${(value * 100).toFixed(1)}%`;
  const m = /^\.(\d+)(f|%)$/.exec(fmt);
  if (m) {
    const digits = Number(m[1]);
    return m[2] === "%" ? `${(value * 100).toFixed(digits)}%` : value.toFixed(digits);
  }
  return trimNumber(value);
}

function trimNumber(v) {
  if (Number.isInteger(v)) return v.toLocaleString();
  if (Math.abs(v) >= 1e6 || (Math.abs(v) < 1e-4 && v !== 0)) return v.toExponential(2);
  return String(Math.round(v * 1e6) / 1e6);
}
