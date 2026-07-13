I need to trace the full call chain to confirm whether there is a minimum `kem_ss` length guard anywhere between the callback return and the HKDF call.

The full call chain is now confirmed. Here is the complete trace and assessment:

**Call chain:**
`cbmpc_pve_encrypt_with_kem` → `c_base_kem_adapter_t::encrypt` → `pve_base_pke_runtime_kem().encrypt` → `kem_pve_base_pke_t<kem_policy_runtime_kem_t>::encrypt` → `kem_aead_ciphertext_t::seal` → `kem_policy_runtime_kem_t::encapsulate` → `kem_encap_shim` → user callback → HKDF

**Guard analysis in `kem_encap_shim`:**

The only `kem_ss` output checks are:
- `kem_ss.size < 0` → `E_FORMAT`
- `kem_ss.size > 0 && !kem_ss.data` → `E_FORMAT`

A callback returning `{kem_ss.size=1, kem_ss.data=valid_ptr}` or `{kem_ss.size=0, kem_ss.data=nullptr}` passes both checks. There is **no minimum length guard**.

**Contrast with `kem_policy_rsa_oaep_hsm_t::decapsulate`**, which explicitly validates:
```cpp
const int expected_ss_size = crypto::hash_alg_t::get(crypto::hash_e::sha256).size;
if (kem_ss.size() != expected_ss_size) return coinbase::error(E_CRYPTO, "invalid RSA KEM output size");
``` [1](#0-0) 

The `kem_policy_runtime_kem_t` path has no equivalent check. [2](#0-1) 

After `encapsulate` returns, `kem_aead_ciphertext_t::seal` immediately feeds `kem_ss` into HKDF with no size validation:
```cpp
buf_t prk = crypto::hkdf_extract_sha256(mem_t(), kem_ss);
buf_t aes_key = crypto::hkdf_expand_sha256(prk, mem_t("CBMPC|KEM-AEAD|v1|..."), 32);
``` [3](#0-2) 

---

### Title
Missing minimum-length guard on callback-supplied `kem_ss` in runtime KEM path allows trivially-weak AES key derivation — (`src/cbmpc/c_api/pve_base_pke.cpp`)

### Summary
`kem_encap_shim` validates that `rho32` (the input) is 32 bytes but applies no minimum-length check to the callback-returned `out_kem_ss`. A malicious `cbmpc_pve_kem_encap_fn` callback can return a 1-byte (or 0-byte) shared secret; the library accepts it, feeds it directly into HKDF-Extract, and derives a 32-byte AES-GCM-256 key from that degenerate input. The resulting ciphertext has at most 256 (or 1) possible AES keys, making the encrypted private scalar trivially recoverable by brute force.

### Finding Description
**Entrypoint:** `cbmpc_pve_encrypt_with_kem` (`src/cbmpc/c_api/pve_base_pke.cpp:315`) [4](#0-3) 

**Shim validation (insufficient):** `kem_encap_shim` checks `rho32.size != 32` (the *input* nonce) and rejects negative or null-with-positive-size `kem_ss`, but does **not** enforce `kem_ss.size >= MIN_SS_BYTES`: [5](#0-4) 

**Policy layer (no check):** `kem_policy_runtime_kem_t::encapsulate` simply forwards the callback result with no post-call size validation: [2](#0-1) 

**HKDF consumption (no check):** `kem_aead_ciphertext_t::seal` immediately uses whatever `kem_ss` was returned: [6](#0-5) 

The RSA-OAEP HSM path demonstrates the library *knows* this check is necessary — it enforces exactly 32 bytes — but the generic runtime KEM path omits it entirely: [7](#0-6) 

### Impact Explanation
The private scalar `x` passed to `cbmpc_pve_encrypt_with_kem` is AES-GCM encrypted under a key derived from the callback-supplied `kem_ss`. With `kem_ss.size=1` there are 256 possible IKM values → 256 possible AES keys. An eavesdropper who obtains the ciphertext can try all 256 keys; AES-GCM authentication will confirm the correct one, yielding `x` (the private scalar). With `kem_ss.size=0` the IKM is empty and the AES key is fully deterministic from public information (label, IV), reducing the brute-force cost to 1.

### Likelihood Explanation
Requires a malicious `cbmpc_pve_kem_encap_fn` callback — e.g., a compromised or adversarial HSM/KEM vendor supplying the callback to a legitimate encryptor. The API documentation lists no minimum `kem_ss` length requirement, so a subtly malicious implementation would not be caught by code review of the callback alone. The library's own API contract (`rho32` is validated; `kem_ss` is not) creates a false sense of completeness.

### Recommendation
In `kem_encap_shim`, add a minimum-length check on `kem_ss` after the callback returns, mirroring the RSA-OAEP HSM path:
```cpp
constexpr int MIN_KEM_SS_BYTES = 16; // or 32 to match built-in policies
if (kem_ss.size < MIN_KEM_SS_BYTES) {
    if (kem_ct.data) cbmpc_cmem_free(kem_ct);
    if (kem_ss.data) cbmpc_cmem_free(kem_ss);
    return E_CRYPTO; // "kem_ss too short"
}
```
Document the minimum `kem_ss` length requirement in `cbmpc_pve_kem_encap_fn`'s API comment in `include/cbmpc/c_api/pve_base_pke.h`. [8](#0-7) 

### Proof of Concept
```c
// Malicious encap callback: returns 1-byte kem_ss
cbmpc_error_t weak_encap(void* ctx, cmem_t ek, cmem_t rho32,
                          cmem_t* out_kem_ct, cmem_t* out_kem_ss) {
    // kem_ct: arbitrary 1-byte value
    out_kem_ct->data = (uint8_t*)cbmpc_malloc(1);
    out_kem_ct->data[0] = 0xAB;
    out_kem_ct->size = 1;
    // kem_ss: 1-byte secret — only 256 possible values
    out_kem_ss->data = (uint8_t*)cbmpc_malloc(1);
    out_kem_ss->data[0] = 0x42;  // attacker knows this
    out_kem_ss->size = 1;
    return CBMPC_SUCCESS;
}

// Corresponding decap callback
cbmpc_error_t weak_decap(void* ctx, cmem_t dk, cmem_t kem_ct,
                          cmem_t* out_kem_ss) {
    out_kem_ss->data = (uint8_t*)cbmpc_malloc(1);
    out_kem_ss->data[0] = 0x42;
    out_kem_ss->size = 1;
    return CBMPC_SUCCESS;
}

// Encrypt a private scalar x
cbmpc_pve_base_kem_t kem = { nullptr, weak_encap, weak_decap };
cmem_t ct = {nullptr, 0};
cbmpc_pve_encrypt_with_kem(&kem, CBMPC_CURVE_SECP256K1, ek, label, x, &ct);
// Returns CBMPC_SUCCESS — library accepts 1-byte kem_ss

// Brute-force recovery: try all 256 possible kem_ss values
for (int b = 0; b <= 255; b++) {
    uint8_t candidate_ss[1] = { (uint8_t)b };
    // Derive AES key: HKDF-Extract(salt=zeros, ikm=candidate_ss)
    // then HKDF-Expand with the known label
    // Attempt AES-GCM decrypt of ct with derived key
    // AES-GCM tag verification succeeds only for b == 0x42
    // → x recovered
}
```

### Citations

**File:** include-internal/cbmpc/internal/protocol/pve_base.h (L128-133)
```text
  static error_t encapsulate(const ek_t& pub_key, buf_t& kem_ct, buf_t& kem_ss, crypto::drbg_aes_ctr_t* drbg) {
    if (!pub_key.callbacks || !pub_key.callbacks->encap) return E_BADARG;
    constexpr int rho_size = 32;
    buf_t rho = drbg ? drbg->gen(rho_size) : crypto::gen_random(rho_size);
    return pub_key.callbacks->encap(pub_key.callbacks->ctx, pub_key.ek_bytes, rho, kem_ct, kem_ss);
  }
```

**File:** include-internal/cbmpc/internal/protocol/pve_base.h (L163-173)
```text
  static error_t decapsulate(const dk_t& prv_key, mem_t kem_ct, buf_t& kem_ss) {
    if (!prv_key.decap) return E_BADARG;
    error_t rv = prv_key.decap(prv_key.ctx, prv_key.dk_handle, kem_ct, kem_ss);
    if (rv) return rv;

    // Our RSA-OAEP KEM policy uses a 32-byte shared secret (SHA-256 output size).
    const int expected_ss_size = crypto::hash_alg_t::get(crypto::hash_e::sha256).size;
    if (kem_ss.size() != expected_ss_size) return coinbase::error(E_CRYPTO, "invalid RSA KEM output size");

    return SUCCESS;
  }
```

**File:** include-internal/cbmpc/internal/crypto/base_pki.h (L49-59)
```text
    buf_t kem_ss;
    if (rv = KEM_POLICY::encapsulate(pub_key, kem_ct, kem_ss, drbg)) return rv;

    buf_t iv_buf = drbg ? drbg->gen(iv_size) : gen_random(iv_size);
    cb_assert(iv_buf.size() == iv_size);
    memmove(iv, iv_buf.data(), iv_size);

    // RFC 5869 HKDF: AES-GCM-256 key derivation from KEM shared secret
    buf_t prk = crypto::hkdf_extract_sha256(mem_t(), kem_ss);
    buf_t aes_key = crypto::hkdf_expand_sha256(prk, mem_t("CBMPC|KEM-AEAD|v1|KDF=HKDF-SHA256|AEAD=AES-GCM-256"), 32);
    crypto::aes_gcm_t::encrypt(aes_key, mem_t(iv, iv_size), aad, tag_size, plain, aead_ciphertext);
```

**File:** src/cbmpc/c_api/pve_base_pke.cpp (L24-50)
```cpp
  if (rho32.size != 32) return E_BADARG;

  cmem_t kem_ct{nullptr, 0};
  cmem_t kem_ss{nullptr, 0};
  const cbmpc_error_t rv = kem->encap(kem->ctx, cmem_t{const_cast<byte_ptr>(ek_bytes.data), ek_bytes.size},
                                      cmem_t{const_cast<byte_ptr>(rho32.data), rho32.size}, &kem_ct, &kem_ss);
  if (rv) {
    if (kem_ct.data) cbmpc_cmem_free(kem_ct);
    if (kem_ss.data) cbmpc_cmem_free(kem_ss);
    return rv;
  }

  if (kem_ct.size < 0 || (kem_ct.size > 0 && !kem_ct.data)) {
    cbmpc_free(kem_ct.data);
    if (kem_ss.data) cbmpc_cmem_free(kem_ss);
    return E_FORMAT;
  }
  if (kem_ss.size < 0 || (kem_ss.size > 0 && !kem_ss.data)) {
    cbmpc_free(kem_ss.data);
    if (kem_ct.data) cbmpc_cmem_free(kem_ct);
    return E_FORMAT;
  }
  out_kem_ct = coinbase::buf_t(kem_ct.data, kem_ct.size);
  out_kem_ss = coinbase::buf_t(kem_ss.data, kem_ss.size);
  cbmpc_cmem_free(kem_ct);
  cbmpc_cmem_free(kem_ss);
  return CBMPC_SUCCESS;
```

**File:** src/cbmpc/c_api/pve_base_pke.cpp (L315-347)
```cpp
cbmpc_error_t cbmpc_pve_encrypt_with_kem(const cbmpc_pve_base_kem_t* kem, cbmpc_curve_id_t curve, cmem_t ek,
                                         cmem_t label, cmem_t x, cmem_t* out_ciphertext) {
  try {
    if (!out_ciphertext) return E_BADARG;
    *out_ciphertext = cmem_t{nullptr, 0};
    if (!kem || !kem->encap) return E_BADARG;

    const auto vek = validate_cmem(ek);
    if (vek) return vek;
    const auto vl = validate_cmem(label);
    if (vl) return vl;
    const auto vx = validate_cmem(x);
    if (vx) return vx;

    coinbase::api::curve_id curve_cpp;
    const auto cconv = to_cpp_curve(curve, curve_cpp);
    if (cconv) return cconv;

    c_base_kem_adapter_t adapter(kem);
    coinbase::buf_t ct;
    const coinbase::error_t rv =
        coinbase::api::pve::encrypt(adapter, curve_cpp, view_cmem(ek), view_cmem(label), view_cmem(x), ct);
    if (rv) return rv;

    return alloc_cmem_from_buf(ct, out_ciphertext);
  } catch (const std::bad_alloc&) {
    if (out_ciphertext) *out_ciphertext = cmem_t{nullptr, 0};
    return E_INSUFFICIENT;
  } catch (...) {
    if (out_ciphertext) *out_ciphertext = cmem_t{nullptr, 0};
    return E_GENERAL;
  }
}
```

**File:** include/cbmpc/c_api/pve_base_pke.h (L141-157)
```text
// Custom KEM encapsulation callback.
//
// - Must be deterministic given `rho32`.
// - Must allocate outputs with `cbmpc_malloc`.
typedef cbmpc_error_t (*cbmpc_pve_kem_encap_fn)(void* ctx, cmem_t ek, cmem_t rho32, cmem_t* out_kem_ct,
                                                cmem_t* out_kem_ss);

// Custom KEM decapsulation callback.
//
// - Must allocate outputs with `cbmpc_malloc`.
typedef cbmpc_error_t (*cbmpc_pve_kem_decap_fn)(void* ctx, cmem_t dk, cmem_t kem_ct, cmem_t* out_kem_ss);

typedef struct cbmpc_pve_base_kem_t {
  void* ctx;
  cbmpc_pve_kem_encap_fn encap;
  cbmpc_pve_kem_decap_fn decap;
} cbmpc_pve_base_kem_t;
```
