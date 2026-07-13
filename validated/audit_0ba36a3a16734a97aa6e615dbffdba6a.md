Now I have all the information needed to analyze this question thoroughly. Let me trace the exact code flow.

**Key code paths:**

**Constructor** (`pve_batch.h:11-14`):
```cpp
explicit ec_pve_batch_t(int batch_count) : n(batch_count), rows(kappa) {
    cb_assert(batch_count > 0 && batch_count <= MAX_BATCH_COUNT);
    Q.resize(n);
}
```
At construction: `n == Q.size()` always. `rows` is sized to `kappa` (not `n`).

**`convert` method** (`pve_batch.h:48-61`):
```cpp
void convert(coinbase::converter_t& converter) {
    if (int(Q.size()) != n) {   // ← guard fires BEFORE Q is deserialized
        converter.set_error();
        return;
    }
    converter.convert(Q, L, b); // ← Q is deserialized HERE, Q.size() may change
    ...
}
```

**`converter_t::convert<std::vector<T>>`** (`convert.h:115-131`):
```cpp
template <typename T>
void convert(std::vector<T>& value) {
    if (!write) value.clear();
    uint32_t count = (uint32_t)value.size();
    convert_len(count);
    if (!write) {
        if (count > MAX_CONTAINER_ELEMENTS) { set_error(); return; }
        value.resize(count);  // ← resizes Q to whatever count is in the blob
    }
    ...
}
```

**`coinbase::convert<T>`** (`convert.h:277-282`):
```cpp
template <typename T>
error_t convert(T& dst, mem_t src) {
    converter_t converter(src);
    converter.convert(dst);
    return converter.get_rv(); // ← no post-deserialization Q.size()==n check
}
```

**Tracing the actual scenario:**

The question's premise that "`n` is deserialized first and used to size `rows`" is factually incorrect — `n` is never serialized in `blob.ct`; it comes from `blob.batch_count` via the constructor, and `rows` is always sized to `kappa`, not `n`.

The question's claimed call sequence — "n deserialized → Q deserialized → Q.size() != n → set_error" — does not match the actual code. The guard fires **before** Q is deserialized, when `Q.size() == n` is guaranteed by the constructor. The guard always passes. After `converter.convert(Q, ...)` runs, Q is resized to whatever count is in `blob.ct`, potentially making `Q.size() != n`, but **no error is set** for this post-deserialization mismatch.

**What actually happens with a mismatched blob:**

- `ec_pve_batch_t pve_ct(n)` → `n=5`, `Q.size()=5` [1](#0-0) 
- `coinbase::convert(pve_ct, blob.ct)` with Q-count=3 in blob → guard passes (5==5), Q deserialized to size 3, **returns SUCCESS** with `n=5`, `Q.size()=3` [2](#0-1) 
- `decrypt` is called with `skip_verify=true` [3](#0-2) 

The question's conclusion — "honest code returns a deserialization error before decrypt is called" — is **incorrect**. The deserialization succeeds with inconsistent state.

**Actual impact analysis:**

For `restore_from_decrypted` to be reached (where `Q[i]` for `i >= Q.size()` would be OOB), `base_pke.decrypt` must succeed on at least one row. This requires the attacker

### Citations

**File:** include-internal/cbmpc/internal/protocol/pve_batch.h (L11-14)
```text
  explicit ec_pve_batch_t(int batch_count) : n(batch_count), rows(kappa) {
    cb_assert(batch_count > 0 && batch_count <= MAX_BATCH_COUNT);
    Q.resize(n);
  }
```

**File:** include-internal/cbmpc/internal/protocol/pve_batch.h (L48-61)
```text
  void convert(coinbase::converter_t& converter) {
    if (int(Q.size()) != n) {
      converter.set_error();
      return;
    }

    converter.convert(Q, L, b);

    for (int i = 0; i < kappa; i++) {
      converter.convert(rows[i].x_bin);
      converter.convert(rows[i].r);
      converter.convert(rows[i].c);
    }
  }
```

**File:** src/cbmpc/api/pve_batch_single_recipient.cpp (L173-174)
```cpp
  rv = pve_ct.decrypt(bridge, coinbase::mpc::pve_keyref(dk_mem), coinbase::mpc::pve_keyref(ek_mem), label, icurve,
                      xs_bn, /*skip_verify=*/true);
```
