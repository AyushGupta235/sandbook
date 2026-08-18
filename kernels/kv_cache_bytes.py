"""Size of a transformer's KV cache.

The formula everyone gets wrong in the same two ways: forgetting the factor of
2 for K and V, and scaling by query heads instead of key/value heads, which
makes grouped-query attention look like it saves nothing.
"""

NAME = "kv_cache_bytes"
SUMMARY = "bytes held by a transformer KV cache for a given architecture and context"
TOLERANCE = 1e-9

PROBES = [
    # Llama-3-8B shaped, GQA with 8 KV heads, fp16, single sequence.
    {"batch": 1, "seq_len": 4096, "n_layers": 32, "n_kv_heads": 8,
     "head_dim": 128, "dtype_bytes": 2},
    # Same architecture as multi-head attention: 32 KV heads instead of 8.
    # A formula keyed on query heads returns this number for both, which is
    # the whole reason this kernel exists.
    {"batch": 1, "seq_len": 4096, "n_layers": 32, "n_kv_heads": 32,
     "head_dim": 128, "dtype_bytes": 2},
    # Batched serving, long context: the case where the cache exceeds weights.
    {"batch": 32, "seq_len": 8192, "n_layers": 32, "n_kv_heads": 8,
     "head_dim": 128, "dtype_bytes": 2},
    # fp8 halves it.
    {"batch": 32, "seq_len": 8192, "n_layers": 32, "n_kv_heads": 8,
     "head_dim": 128, "dtype_bytes": 1},
    # Multi-query attention: one KV head.
    {"batch": 4, "seq_len": 2048, "n_layers": 80, "n_kv_heads": 1,
     "head_dim": 128, "dtype_bytes": 2},
    # Nothing cached yet, at the very start of a request.
    {"batch": 1, "seq_len": 0, "n_layers": 32, "n_kv_heads": 8,
     "head_dim": 128, "dtype_bytes": 2},
]


def reference(batch, seq_len, n_layers, n_kv_heads, head_dim, dtype_bytes):
    """Bytes of KV cache held for `batch` sequences of `seq_len` tokens.

    Two vectors per position per KV head per layer, one K and one V, each
    `head_dim` values wide at `dtype_bytes` each. Query heads do not appear:
    a query is used at the step that produces it and then discarded.
    """
    for name, value in (("batch", batch), ("seq_len", seq_len), ("n_layers", n_layers),
                        ("n_kv_heads", n_kv_heads), ("head_dim", head_dim),
                        ("dtype_bytes", dtype_bytes)):
        if value < 0:
            raise ValueError(f"{name} cannot be negative")

    total = 2 * batch * seq_len * n_layers * n_kv_heads * head_dim * dtype_bytes
    return {
        "bytes": total,
        "gib": total / (1024 ** 3),
        "bytes_per_token": 2 * n_layers * n_kv_heads * head_dim * dtype_bytes,
    }
