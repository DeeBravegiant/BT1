## Code Trace

**Entry point:** `cbmpc_ecdsa_mp_get_public_key_compressed` → `coinbase::api::ecdsa_mp::get_public_key_compressed`

The C API wrapper delegates directly to the internal function with no additional validation: [1](#0-0) 

The internal `get_public_key_compressed` function: [2](#0-1) 

It accepts both `key_blob_version_v1` (1) and `ac_key_blob_version_v1` (2), decodes `blob.Q_compressed` into a point, and returns it — **with no check that Q equals the sum of all Qis**.

**Contrast with `deserialize_key_blob` (v1/additive path)**, which does enforce the invariant: [3](#0-2) 

**And `deserialize_ac_key_blob` (v2/AC path)**, which explicitly omits the check by design: [4](#0-3) 

The comment at line 233 says "validated using the access structure at use sites" — but `get_public_key_compressed` is a use site that performs no such validation.

The only per-share binding enforced in the AC deserializer is `x_share * G == Qi_self`. An attacker can satisfy this while setting `Q_compressed` to an arbitrary valid curve point Q' ≠ sum(Qis).

---

### Title
Missing Q == sum(Qis) Consistency Check in `get_public_key_compressed` for AC Key Blobs — (`src/cbmpc/api/ecdsa_mp.cpp`)

### Summary
`get_public_key_compressed` accepts v2 (AC) key blobs and returns `blob.Q_compressed` verbatim without verifying Q equals the sum of all Qis. An attacker supplying a crafted v2 blob with Q' ≠ sum(Qis) receives Q' back as the public key with `SUCCESS`, causing honest code to register a wrong public key that does not correspond to the actual signing key.

### Finding Description
`get_public_key_compressed` uses its own lightweight parser (`parse_key_blob_any_version`) rather than the full `deserialize_key_blob` / `deserialize_ac_key_blob` paths. It performs only:
- Size bound check
- Version check (v1 or v2)
- Curve validity check
- `Q.from_bin(curve, blob.Q_compressed)` — point decoding only

No check of the form `Q == sum(Qis)` is performed for either version. For v1 blobs, `deserialize_key_blob` enforces this invariant at lines 172–174, but `get_public_key_compressed` bypasses that path entirely. For v2 blobs, `deserialize_ac_key_blob` intentionally omits the check (relying on "use sites"), but `get_public_key_compressed` is a use site that never performs it.

A crafted v2 blob satisfying:
- `x_share ∈ [0, q)` ✓
- `x_share * G == Qi_self` ✓ (self-share binding satisfied)
- `Q_compressed` encodes an arbitrary attacker-chosen point Q' ≠ sum(Qis)

will pass all validation in `get_public_key_compressed` and return Q' with `SUCCESS`. [5](#0-4) 

### Impact Explanation
Honest code that calls `cbmpc_ecdsa_mp_get_public_key_compressed` on a crafted blob receives Q' as the authoritative public key. If the attacker chose Q' = x'·G for a scalar x' they control, they can produce valid ECDSA signatures under x' that verify under Q'. Honest code that registered Q' as the verification key will accept those attacker-produced signatures as legitimate — a complete key substitution. Meanwhile, signatures produced by the actual MPC signing protocol (under sum(x_shares), corresponding to sum(Qis)) will fail to verify under Q', breaking honest-party signature acceptance.

This fits: **High — attacker-controlled blob accepted under the wrong key**.

### Likelihood Explanation
`cbmpc_ecdsa_mp_get_public_key_compressed` is a public C API that takes a raw `cmem_t key_blob` with no authentication or integrity protection. Any caller who can supply a blob — including a malicious serialized-input provider or a party that stores and re-presents blobs — can trigger this path. Constructing a valid crafted blob requires only knowledge of the blob serialization format (which is public) and the ability to pick a curve point Q' and a scalar x' with x'·G = Qi_self.

### Recommendation
In `get_public_key_compressed`, after decoding all Qis, compute `Q_sum = sum(Qis)` and reject if `Q != Q_sum`. This is already done for v1 blobs in `deserialize_key_blob` and should be applied consistently in the public-key extraction path for both blob versions.

### Proof of Concept
```cpp
// 1. Run honest DKG to get a legitimate v2 blob for party "p0".
// 2. Parse the blob bytes; locate and replace Q_compressed with
//    an attacker-chosen point Q' = x'*G (x' known to attacker).
//    Keep x_share and Qi_self consistent (x_share * G == Qi_self).
// 3. Re-serialize the blob with the modified Q_compressed.
// 4. Call cbmpc_ecdsa_mp_get_public_key_compressed on the crafted blob.
// 5. Assert return == CBMPC_SUCCESS.
// 6. Assert returned bytes == compressed(Q') != compressed(sum(Qis)).
// This demonstrates the function returns the wrong public key without error.
```

### Citations

**File:** src/cbmpc/c_api/ecdsa_mp.cpp (L340-359)
```cpp
cbmpc_error_t cbmpc_ecdsa_mp_get_public_key_compressed(cmem_t key_blob, cmem_t* out_pub_key) {
  try {
    if (!out_pub_key) return E_BADARG;
    *out_pub_key = cmem_t{nullptr, 0};
    const auto vkb = validate_cmem(key_blob);
    if (vkb) return vkb;

    coinbase::buf_t pk;
    const coinbase::error_t rv = coinbase::api::ecdsa_mp::get_public_key_compressed(view_cmem(key_blob), pk);
    if (rv) return rv;

    return alloc_cmem_from_buf(pk, out_pub_key);
  } catch (const std::bad_alloc&) {
    if (out_pub_key) *out_pub_key = cmem_t{nullptr, 0};
    return E_INSUFFICIENT;
  } catch (...) {
    if (out_pub_key) *out_pub_key = cmem_t{nullptr, 0};
    return E_GENERAL;
  }
}
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L172-174)
```cpp
  coinbase::crypto::ecc_point_t Q_sum = curve.infinity();
  for (const auto& kv : Qis) Q_sum += kv.second;
  if (Q != Q_sum) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L233-238)
```cpp
  // Access-structure key blobs are validated using the access structure at use sites.
  // Here we only enforce the self-share binding.
  const auto& G = curve.generator();
  const auto it_self = Qis.find(blob.party_name);
  if (it_self == Qis.end()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.x_share * G != it_self->second) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L459-479)
```cpp
error_t get_public_key_compressed(mem_t key_blob, buf_t& pub_key) {
  if (const error_t rv = coinbase::api::detail::validate_mem_arg_max_size(key_blob, "key_blob",
                                                                          coinbase::api::detail::MAX_OPAQUE_BLOB_SIZE))
    return rv;
  key_blob_v1_t blob;
  error_t rv = coinbase::convert(blob, key_blob);
  if (rv) return rv;
  if (blob.version != key_blob_version_v1 && blob.version != ac_key_blob_version_v1)
    return coinbase::error(E_FORMAT, "unsupported key blob version");

  const auto cid = static_cast<curve_id>(blob.curve);
  if (cid == curve_id::ed25519) return coinbase::error(E_FORMAT, "invalid key blob curve");
  const auto curve = to_internal_curve(cid);
  if (!curve.valid()) return coinbase::error(E_FORMAT, "invalid key blob curve");

  coinbase::crypto::ecc_point_t Q(curve);
  if (rv = Q.from_bin(curve, blob.Q_compressed)) return coinbase::error(rv, "invalid key blob");

  pub_key = Q.to_compressed_bin();
  return SUCCESS;
}
```
