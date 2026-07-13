## Tracing the Vulnerability

Let me trace the exact code path to determine if this is real.

### Step 1: Deserialization in `decrypt_batch`

`decrypt_batch` parses the outer blob, extracts `n = blob.batch_count` (attacker-controlled = 4), constructs `ec_pve_batch_t pve_ct(n)` (which sets `Q.resize(4)`), then calls `coinbase::convert(pve_ct, blob.ct)`. [1](#0-0) 

### Step 2: The `convert()` guard fires BEFORE deserialization

The guard in `ec_pve_batch_t::convert()` checks `Q.size() != n` **before** calling `converter.convert(Q, ...)`: [2](#0-1) 

At the time of the check, `Q.size() == n == 4` (set by the constructor), so the guard passes. Then `converter.convert(Q, ...)` deserializes Q from the attacker-controlled stream.

### Step 3: Vector deserialization overwrites Q's size

The generic vector deserializer clears the vector, reads the count from the stream, and resizes to that count: [3](#0-2) 

If the attacker serialized Q with count=2, after deserialization `Q.size() == 2` but `n == 4`. No post-deserialization check exists in `convert()` to catch this invariant violation.

### Step 4: `verify()` is skipped — the only post-deserialization guard

`verify()` has the correct check: [4](#0-3) 

But `decrypt()` skips it when `skip_verify=true`: [5](#0-4) 

And `decrypt_batch` hardcodes `skip_verify=true`: [6](#0-5) 

### Step 5: Out-of-bounds access in `restore_from_decrypted`

The loop iterates `for (int i = 0; i < n; i++)` using `n=4`, but `Q.size()==2`: [7](#0-6) 

When `i >= 2`, `Q[i]` is an out-of-bounds `std::vector` access — undefined behavior.

### Step 6: Attacker can reach `restore_from_decrypted`

The attacker knows `ek` (public) and controls the inner Q points, so they can compute `inner_label = genPVELabelWithPoint(label, Q)` (using their 2-point Q) and craft `rows[i].c` as a valid encryption under `ek` with that label. The size checks inside `restore_from_decrypted` (`row.x_bin.size() != n * curve_size`, `x1_bin.size != n * curve_size`) can be satisfied by the attacker setting those fields to `4 * curve_size` in the crafted blob.

---

### Title
Out-of-bounds access in `ec_pve_batch_t::restore_from_decrypted` via mismatched `batch_count` and deserialized Q-vector size when `skip_verify=true` — (`src/cbmpc/protocol/pve_batch.cpp`, `include-internal/cbmpc/internal/protocol/pve_batch.h`)

### Summary
The `ec_pve_batch_t::convert()` method checks `Q.size() == n` before deserialization but not after. An attacker can craft a `pve_batch_ciphertext_blob_v1_t` with `batch_count=N` but an inner Q-vector of size M < N. After deserialization, `n=N` but `Q.size()=M`. Because `decrypt_batch` calls `ec_pve_batch_t::decrypt` with `skip_verify=true` (hardcoded), the only post-deserialization guard (`verify()`) is bypassed. `restore_from_decrypted` then iterates `for (int i = 0; i < n; i++)` and accesses `Q[i]` out-of-bounds for `i >= M`.

### Finding Description
**Root cause**: `ec_pve_batch_t::convert()` enforces `Q.size() == n` only as a pre-deserialization guard. The generic `converter_t::convert(std::vector<T>&)` clears and resizes the vector to whatever count is encoded in the stream, silently breaking the invariant. No post-deserialization check restores it.

**Bypass**: `ec_pve_batch_t::verify()` contains the correct post-deserialization check (`if (int(Q.size()) != n) return E_BADARG`), but `decrypt_batch` hardcodes `skip_verify=true`, making this the sole guard and leaving it permanently disabled on the decrypt path.

**Trigger path**:
1. Attacker crafts outer blob: `batch_count=4`, inner `blob.ct` = serialization of an `ec_pve_batch_t` where Q count=2, `rows[i].x_bin.size()=4*curve_size`, `rows[i].r.size()=32`, and `rows[i].c` = valid encryption under `ek` of a `4*curve_size`-byte buffer using `inner_label = genPVELabelWithPoint(label, Q_2pts)`.
2. `decrypt_batch` → `parse_batch_ciphertext` (passes, `batch_count=4` is valid) → `ec_pve_batch_t pve_ct(4)` (Q.resize(4)) → `coinbase::convert(pve_ct, blob.ct)` (guard passes, Q deserialized to size 2, n stays 4, returns SUCCESS).
3. `pve_ct.decrypt(..., skip_verify=true)` → skips `verify()` → `base_pke.decrypt` succeeds on the crafted row → `restore_from_decrypted(i, x_buf, curve, xs)`.
4. Inside `restore_from_decrypted`: size checks pass (attacker set `x_bin` and decrypted buffer to `4*curve_size`), loop runs `i=0..3`, `Q[2]` and `Q[3]` are out-of-bounds accesses on a size-2 vector.

### Impact Explanation
Out-of-bounds read on `std::vector<ecc_point_t>` is undefined behavior. In practice this either crashes the process (denial of service) or reads adjacent heap memory as an `ecc_point_t`. If the adjacent memory happens to satisfy `Q[i] == x[i] * G` (or the comparison itself is corrupted), `restore_from_decrypted` returns `SUCCESS` with attacker-influenced scalars in `xs`, which the caller accepts as legitimately decrypted key material. This constitutes accepted invalid cryptographic output from the PVE batch decrypt API.

### Likelihood Explanation
The attack requires only the ability to supply a crafted ciphertext blob to `decrypt_batch` — a standard API input. The attacker needs a valid `ek` (public, always available) to craft one valid row ciphertext. No threshold collusion, no key material, and no privileged access is required. The `skip_verify=true` default is hardcoded in the public API wrapper, so no caller configuration can prevent it.

### Recommendation
1. **Add a post-deserialization invariant check** in `ec_pve_batch_t::convert()`: after `converter.convert(Q, L, b)`, when `!converter.is_write()`, verify `int(Q.size()) == n` and call `converter.set_error()` if not.
2. **Remove the `skip_verify=true` default** from `decrypt_batch`, or at minimum add an explicit `Q.size() == n` guard in `ec_pve_batch_t::decrypt()` before calling `restore_from_decrypted`, independent of `skip_verify`.

### Proof of Concept
```
1. Generate a valid (ek, dk) keypair.
2. Craft inner_ec_pve_batch:
   - Serialize Q with count=2, two arbitrary valid curve points P0, P1.
   - Serialize L = label.
   - Serialize b = any 128-bit value.
   - For each of kappa rows:
       - x_bin = 4 * curve_size zero bytes  (passes size check for n=4)
       - r = 32 zero bytes
       - c = encrypt(ek, genPVELabelWithPoint(label, [P0,P1]), 4*curve_size zero bytes)
         (one row; others can be garbage)
3. Craft outer blob: version=1, batch_count=4, ct=inner_ec_pve_batch bytes.
4. Call decrypt_batch(curve, dk, ek, outer_blob, label, out_xs).
5. Assert: call returns an error (E_CRYPTO or similar).
   Observed: call either crashes (SIGSEGV/heap corruption) or returns SUCCESS
   with garbage scalars, demonstrating the invariant violation.
```

### Citations

**File:** src/cbmpc/api/pve_batch_single_recipient.cpp (L156-163)
```cpp
  pve_batch_ciphertext_blob_v1_t blob;
  if (rv = parse_batch_ciphertext(ciphertext, blob)) return rv;

  const int n = static_cast<int>(blob.batch_count);

  base_pke_bridge_t bridge(base_pke);
  coinbase::mpc::ec_pve_batch_t pve_ct(n);
  if (rv = coinbase::convert(pve_ct, blob.ct)) return rv;
```

**File:** src/cbmpc/api/pve_batch_single_recipient.cpp (L173-174)
```cpp
  rv = pve_ct.decrypt(bridge, coinbase::mpc::pve_keyref(dk_mem), coinbase::mpc::pve_keyref(ek_mem), label, icurve,
                      xs_bn, /*skip_verify=*/true);
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

**File:** include-internal/cbmpc/internal/core/convert.h (L114-131)
```text
  template <typename T>
  void convert(std::vector<T>& value) {
    if (!write) value.clear();

    uint32_t count = (uint32_t)value.size();
    convert_len(count);

    if (!write) {
      if (count > MAX_CONTAINER_ELEMENTS) {
        set_error();
        return;
      }
      value.resize(count);
    }
    for (uint32_t i = 0; i < count && !is_error(); i++) {
      convert(value[i]);
    }
  }
```

**File:** src/cbmpc/protocol/pve_batch.cpp (L77-78)
```cpp
  if (n <= 0 || n > MAX_BATCH_COUNT) return coinbase::error(E_BADARG);
  if (int(Q.size()) != n) return coinbase::error(E_BADARG);
```

**File:** src/cbmpc/protocol/pve_batch.cpp (L181-184)
```cpp
  for (int i = 0; i < n; i++) {
    MODULO(q) x[i] = x0[i] + x1[i];
    if (Q[i] != x[i] * G) return coinbase::error(E_CRYPTO);
  }
```

**File:** src/cbmpc/protocol/pve_batch.cpp (L192-193)
```cpp
  xs.resize(n);
  if (!skip_verify && (rv = verify(base_pke, ek, Q, label))) return rv;
```
