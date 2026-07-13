I need to trace the full code path to determine if this is a real vulnerability.

## Code Path Trace

**Step 1: `c_base_pke_adapter_t::encrypt` guard check** [1](#0-0) 

The guard is:
```cpp
if (out.size < 0 || (out.size > 0 && !out.data)) {
    cbmpc_free(out.data);
    return E_FORMAT;
}
out_ct = coinbase::buf_t(out.data, out.size);
```

When the callback returns `{nullptr, 0}`: `out.size < 0` is false, `(out.size > 0 && !out.data)` is false. The guard does **not** fire. `out_ct = buf_t(nullptr, 0)` — an empty `buf_t` — is accepted and `CBMPC_SUCCESS` is returned.

**Step 2: `ec_pve_t::encrypt` stores empty ciphertexts** [2](#0-1) 

Both `c0[i]` and `c1[i]` become empty `buf_t`s. No emptiness check exists. The challenge `b` is then computed:

```cpp
b_local = crypto::ro::hash_string(Q_local, label, c0, c1, X0, X1).bitlen(kappa);
```

The `encode_and_update` for a C-style array of `buf_t` calls `get_bin_size(buf)` → 0, then hashes 0 bytes per slot. The challenge `b` is computed consistently from all-empty ciphertexts.

**Step 3: `ec_pve_t::verify` with the same malicious callback** [3](#0-2) 

During verify, `base_pke.encrypt(...)` is called again with the same malicious callback → returns empty `buf_t` again → `c0[i]` is empty. `c1[i] = c[i]` (also empty, from the stored ciphertext). The `X0[i]` and `X1[i]` values are recomputed from the same `xi` (derived from stored `x_rows[i]` or `r[i]`), so they match exactly. Therefore:

```cpp
buf_t b_tag = crypto::ro::hash_string(Q, label, c0, c1, X0, X1).bitlen(kappa);
if (b_tag != b) return coinbase::error(E_CRYPTO, "b' != b");
```

`b_tag == b` because both are computed from identical inputs (empty ciphertexts, same `Q`, `label`, `X0`, `X1`). **`verify()` returns `SUCCESS`.**

**Step 4: `ec_pve_t::decrypt` outcome** [4](#0-3) 

`base_pke.decrypt(dk, inner_label, c[i], x_buf)` is called with empty `c[i]`. The malicious decrypt callback receives an empty ciphertext. `restore_from_decrypted` checks `x_value * G != Q` — this is a real cryptographic guard that prevents the attacker from forging a valid decryption without knowing `x`.

---

## Assessment

The proposed exploit path is **real and reachable**:

- The guard `(out.size > 0 && !out.data)` in `c_base_pke_adapter_t::encrypt` is insufficient — it does not reject `{size=0, data=nullptr}`.
- `ec_pve_t::encrypt` stores empty `c[i]` without any non-emptiness check.
- `ec_pve_t::verify` re-invokes the same callback, gets the same empty outputs, and the Fiat-Shamir challenge matches — **verify passes**.
- The concrete bad-output path is: `cbmpc_pve_encrypt` → SUCCESS (ciphertext with empty base PKE slots) → `cbmpc_pve_verify` → SUCCESS.

The impact is **Medium: unsafe state acceptance**. The PVE ciphertext with empty base PKE ciphertexts is accepted and publicly verified as valid, breaking the publicly-verifiable encryption invariant. Decrypt fails at the `x_value * G != Q` check, so no secret recovery occurs — but the verifier is deceived into accepting a structurally invalid ciphertext as a valid commitment.

The API documentation (`include/cbmpc/c_api/pve_base_pke.h`) does not explicitly require non-empty output, and the library does not enforce it.

---

### Title
Missing non-empty ciphertext guard in `c_base_pke_adapter_t::encrypt` allows malicious callback to produce PVE ciphertexts accepted by `verify()` — (`src/cbmpc/c_api/pve_internal.h`)

### Summary
A malicious `cbmpc_pve_base_pke_encrypt_fn` callback that returns `{nullptr, 0}` with `CBMPC_SUCCESS` bypasses the only ABI guard in `c_base_pke_adapter_t::encrypt`, causing `ec_pve_t::encrypt` to store empty base-PKE ciphertexts. Because `ec_pve_t::verify` re-invokes the same callback and computes the Fiat-Shamir challenge from the same empty values, the challenge matches and `verify()` returns `SUCCESS` for the structurally invalid ciphertext.

### Finding Description
In `src/cbmpc/c_api/pve_internal.h`, `c_base_pke_adapter_t::encrypt` checks:

```cpp
if (out.size < 0 || (out.size > 0 && !out.data)) { return E_FORMAT; }
out_ct = coinbase::buf_t(out.data, out.size);
``` [5](#0-4) 

The condition `(out.size > 0 && !out.data)` only catches null-pointer with positive size. It does not reject `{size=0, data=nullptr}`. The adapter returns `CBMPC_SUCCESS` with an empty `buf_t`.

`ec_pve_t::encrypt` calls `base_pke.encrypt(...)` twice per row and stores the results in `c0[i]` / `c1[i]` with no post-call emptiness check: [6](#0-5) 

The Fiat-Shamir challenge is then computed over these empty buffers: [7](#0-6) 

`ec_pve_t::verify` re-invokes the same callback, obtains the same empty `c0[i]`, reads `c1[i] = c[i]` (also empty), recomputes the same `X0`/`X1` from stored randomness, and arrives at the same challenge hash: [8](#0-7) 

`b_tag == b` → `verify()` returns `SUCCESS`.

### Impact Explanation
The PVE scheme's public verifiability guarantee is broken for callers using a custom base PKE. A verifier calling `cbmpc_pve_verify` (or the higher-level `coinbase::api::pve::verify`) is deceived into accepting a ciphertext that contains no actual encrypted material. Any downstream system that gates on `verify()` success — e.g., a key-recovery workflow that proceeds only after verification — will treat the invalid ciphertext as a valid commitment. Decrypt subsequently fails at the `x_value * G != Q` check, so no secret is recovered, but the unsafe acceptance of the verified state is the concrete impact.

### Likelihood Explanation
Requires a caller to supply a custom `cbmpc_pve_base_pke_t` callback. This is an explicitly supported API path. A buggy or adversarial callback implementation that returns empty output on success (e.g., an HSM stub that silently fails) would trigger this path without any deliberate malice.

### Recommendation
Add a non-empty check immediately after the ABI guard in `c_base_pke_adapter_t::encrypt`:

```cpp
if (out.size == 0) {
    return coinbase::error(E_FORMAT, "base PKE callback returned empty ciphertext");
}
```

Similarly add the same check in the `decrypt` path of `c_base_pke_adapter_t` for symmetry. Document in `include/cbmpc/c_api/pve_base_pke.h` that `out_ct->size` must be strictly positive on success.

### Proof of Concept
```c
// Malicious callback: always returns empty ciphertext with SUCCESS
cbmpc_error_t empty_encrypt(void*, cmem_t, cmem_t, cmem_t, cmem_t, cmem_t* out_ct) {
    out_ct->data = nullptr;
    out_ct->size = 0;
    return CBMPC_SUCCESS;
}
cbmpc_error_t empty_decrypt(void*, cmem_t, cmem_t, cmem_t, cmem_t* out) {
    out->data = nullptr; out->size = 0;
    return CBMPC_SUCCESS;
}

cbmpc_pve_base_pke_t bad_pke = { nullptr, empty_encrypt, empty_decrypt };

cmem_t ct = {nullptr, 0};
// Step 1: encrypt succeeds with empty base PKE ciphertexts
assert(cbmpc_pve_encrypt(&bad_pke, CBMPC_CURVE_SECP256K1, ek, label, x, &ct) == CBMPC_SUCCESS);

// Step 2: verify also succeeds — the Fiat-Shamir challenge matches
// because verify re-invokes the same callback and gets the same empty outputs
assert(cbmpc_pve_verify(&bad_pke, CBMPC_CURVE_SECP256K1, ek, ct, Q_compressed, label) == CBMPC_SUCCESS);

// Step 3: decrypt fails at x_value * G != Q (no secret recovery)
// but the unsafe accepted state has already been established
```

### Citations

**File:** src/cbmpc/c_api/pve_internal.h (L27-34)
```text
    if (out.size < 0 || (out.size > 0 && !out.data)) {
      // Callback violated the ABI contract; do not attempt to read.
      cbmpc_free(out.data);
      return E_FORMAT;
    }
    out_ct = coinbase::buf_t(out.data, out.size);
    cbmpc_cmem_free(out);
    return CBMPC_SUCCESS;
```

**File:** src/cbmpc/protocol/pve.cpp (L41-65)
```cpp
    if (rv = base_pke.encrypt(ek, inner_label, x0.to_bin(), rho0, c0[i])) return rv;
    X0[i] = x0 * G;
    if (rv = base_pke.encrypt(ek, inner_label, x1.to_bin(), rho1, c1[i])) return rv;
    X1[i] = Q_local - X0[i];

    x_rows_local[i] = x1;  // output. will be cleared out if later bi == 0
  }

  buf128_t b_local;
  b_local = crypto::ro::hash_string(Q_local, label, c0, c1, X0, X1).bitlen(kappa);

  for (int i = 0; i < kappa; i++) {
    bool bi = b_local.get_bit(i);
    r_local[i] = bi ? r1[i] : r0[i];
    c_local[i] = bi ? c0[i] : c1[i];
    if (!bi) x_rows_local[i] = 0;  // clear the output
  }

  Q = std::move(Q_local);
  L = std::move(L_local);
  b = b_local;
  for (int i = 0; i < kappa; i++) {
    x_rows[i] = std::move(x_rows_local[i]);
    r[i] = r_local[i];
    c[i] = std::move(c_local[i]);
```

**File:** src/cbmpc/protocol/pve.cpp (L87-114)
```cpp
  for (int i = 0; i < kappa; i++) {
    if (x_rows[i].get_bin_size() > max_scalar_size) return coinbase::error(E_CRYPTO);
    if (!q.is_in_range(x_rows[i])) return coinbase::error(E_CRYPTO);

    bool bi = b.get_bit(i);

    crypto::drbg_aes_ctr_t drbg(r[i]);

    bn_t xi;
    if (bi)
      xi = x_rows[i];
    else
      xi = drbg.gen_bn(q);
    buf_t rho = drbg.gen(rho_size);

    X0[i] = xi * G;
    X1[i] = Q - X0[i];
    if (rv = base_pke.encrypt(ek, inner_label, xi.to_bin(), rho, c0[i])) return rv;
    c1[i] = c[i];

    if (bi) {
      std::swap(X0[i], X1[i]);
      std::swap(c0[i], c1[i]);
    }
  }

  buf_t b_tag = crypto::ro::hash_string(Q, label, c0, c1, X0, X1).bitlen(kappa);
  if (b_tag != b) return coinbase::error(E_CRYPTO, "b' != b");
```

**File:** src/cbmpc/protocol/pve.cpp (L154-163)
```cpp
  for (int i = 0; i < kappa; i++) {
    buf_t x_buf;
    if (rv = base_pke.decrypt(dk, inner_label, c[i], x_buf)) continue;
    if (restore_from_decrypted(i, x_buf, curve, x_out) == SUCCESS) {
      return SUCCESS;
    }
  }

  x_out = 0;
  return coinbase::error(E_CRYPTO);
```
