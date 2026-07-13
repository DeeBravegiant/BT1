I have enough information to render a complete verdict. Let me trace the exact code path.

### Title
Zero Private Share Accepted via Infinity Point Bypass in `deserialize_ac_key_blob` — (`src/cbmpc/api/ecdsa_mp.cpp`)

---

### Summary

Both overloads of `deserialize_ac_key_blob` in `src/cbmpc/api/ecdsa_mp.cpp` accept a crafted key blob where `x_share = 0` and `Qis_compressed[party_name]` encodes the point at infinity. The share-binding check `blob.x_share * G != it_self->second` evaluates to `infinity != infinity` → `false`, so no error is returned. The function returns `SUCCESS` with a zero private share stored in the key. This is reachable through the public `sign_ac` API on P256 key blobs.

---

### Finding Description

**Entrypoint**: `coinbase::api::ecdsa_mp::sign_ac` → `deserialize_ac_key_blob(ac_key_blob, ac_key)` (no-job overload, line 402).

**No-job overload** (`src/cbmpc/api/ecdsa_mp.cpp`, lines 248–285):

**Step 1 — Range check passes for zero:** [1](#0-0) [2](#0-1) 

`is_in_range` is defined as `a.sign() >= 0 && a < m`. For `x_share = 0`: both conditions hold (`0 >= 0` and `0 < q`). Zero is not a valid private key share (it is the additive identity, `0 * G = ∞`), but the range check does not exclude it.

**Step 2 — `from_bin` accepts the point at infinity for P256:** [3](#0-2) 

`ecurve_ossl_t::from_bin` explicitly handles `bin[0] == 0` as the infinity encoding. A 33-byte all-zero buffer (valid compressed-point size for P256) passes the size check, sets `bin.size = 1`, and `EC_POINT_oct2point` with a single `0x00` byte sets the point to infinity. No error is returned.

**Step 3 — No `curve.check()` on individual Qi points:** [4](#0-3) 

After `Qi.from_bin` succeeds, the code stores the infinity point directly into `Qis` without calling `curve.check()`. `curve.check()` would have caught infinity: [5](#0-4) 

**Step 4 — Share-binding check trivially passes:** [6](#0-5) 

`blob.x_share * G` = `0 * G` = infinity. `it_self->second` = infinity. The comparison `infinity != infinity` is `false`, so the `return coinbase::error(E_FORMAT, ...)` branch is never taken. The function returns `SUCCESS`.

The same structural flaw exists in the job-bound overload (lines 189–246) used by `refresh_ac`. [7](#0-6) 

**Scope note**: For secp256k1, `ecurve_secp256k1_t::from_bin` delegates to `secp256k1_eckey_pubkey_parse`, which rejects the infinity encoding (only accepts 0x02/0x03/0x04-prefixed points). The attack is confirmed reachable on **P256** key blobs. The ECDSA-MP API explicitly supports P256. [8](#0-7) 

---

### Impact Explanation

A party controlling their own key blob can craft a P256 AC key blob with `x_share = 0` and `Qis[self] = ∞`, pass it to `sign_ac`, and have it accepted as a valid key share. The signing protocol then runs with a zero private share. This is a **public API reachable validation bypass in signing that creates accepted invalid cryptographic output** — the share-binding invariant (`x_share * G == Qi_self` for a non-trivial share) is not enforced. The zero share also means the party contributes nothing to the reconstructed additive share, producing a signature that fails verification against the correct public key, and potentially leaking information about co-signers' shares through protocol messages depending on the signing protocol's structure.

---

### Likelihood Explanation

Any caller of `sign_ac` who controls their own key blob (the normal API usage model) can trigger this. No threshold collusion is required — a single party manipulating their own blob suffices. The crafted blob is trivial to construct (zero scalar, all-zero compressed point encoding).

---

### Recommendation

1. **Reject `x_share = 0` explicitly** in all `deserialize_*_key_blob` functions. The valid range for a private key share is `[1, q-1]`, not `[0, q)`.
2. **Call `curve.check(Qi)` on every deserialized Qi point** in both overloads of `deserialize_ac_key_blob` (and `deserialize_key_blob`). `curve.check` rejects infinity by default and validates subgroup membership.
3. Consider adding an explicit `if (blob.x_share == 0) return coinbase::error(E_FORMAT, "invalid key blob")` guard immediately after the range check.

---

### Proof of Concept

```cpp
// Craft a P256 AC key blob with x_share=0 and Qis['alice'] = point at infinity
key_blob_v1_t blob;
blob.version = ac_key_blob_version_v1;  // 2
blob.curve = static_cast<uint32_t>(curve_id::p256);
blob.party_name = "alice";
blob.x_share = 0;  // zero scalar — passes is_in_range

// 33-byte all-zero buffer = infinity encoding for P256 (compressed size = 1 + 32)
buf_t inf_point(33);
inf_point.bzero();
blob.Qis_compressed["alice"] = inf_point;
blob.Q_compressed = inf_point;  // global Q also infinity (no check in no-job overload)

buf_t serialized = coinbase::convert(blob);

coinbase::mpc::ecdsampc::key_t key;
error_t rv = deserialize_ac_key_blob(mem_t(serialized), key);
// rv == SUCCESS  (expected: E_FORMAT)
// key.x_share == 0 — zero private share accepted as valid
```

### Citations

**File:** src/cbmpc/api/ecdsa_mp.cpp (L233-238)
```cpp
  // Access-structure key blobs are validated using the access structure at use sites.
  // Here we only enforce the self-share binding.
  const auto& G = curve.generator();
  const auto it_self = Qis.find(blob.party_name);
  if (it_self == Qis.end()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.x_share * G != it_self->second) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L262-263)
```cpp
  const coinbase::crypto::mod_t& q = curve.order();
  if (!q.is_in_range(blob.x_share)) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L268-273)
```cpp
  coinbase::crypto::ss::party_map_t<coinbase::crypto::ecc_point_t> Qis;
  for (const auto& kv : blob.Qis_compressed) {
    coinbase::crypto::ecc_point_t Qi;
    if (rv = Qi.from_bin(curve, kv.second)) return coinbase::error(rv, "invalid key blob");
    Qis[kv.first] = std::move(Qi);
  }
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L275-278)
```cpp
  const auto& G = curve.generator();
  const auto it_self = Qis.find(blob.party_name);
  if (it_self == Qis.end()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.x_share * G != it_self->second) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/crypto/base_mod.cpp (L88-88)
```cpp
bool mod_t::is_in_range(const bn_t& a) const { return a.sign() >= 0 && a < m; }
```

**File:** src/cbmpc/crypto/base_ecc.cpp (L317-330)
```cpp
error_t ecurve_ossl_t::from_bin(ecc_point_t& P, mem_t bin) const {
  if (bin.size > 0 && bin[0] == 0)  // infinity
  {
    if (bin.size != 1 + size() && bin.size != 1 + size() * 2) return coinbase::error(E_FORMAT);
    for (int i = 0; i < bin.size; i++)
      if (bin[i]) return coinbase::error(E_CRYPTO);
    bin.size = 1;
  }

  if (0 >= EC_POINT_oct2point(group, P, bin.data, bin.size, bn_t::thread_local_storage_bn_ctx())) {
    return openssl_error("EC_POINT_oct2point error, data-size=" + strext::itoa(bin.size));
  }
  return SUCCESS;
}
```

**File:** src/cbmpc/crypto/base_ecc.cpp (L592-601)
```cpp
error_t ecurve_t::check(const ecc_point_t& point) const {
  if (!point.valid()) return crypto::error("EC-point invalid");
  if (point.get_curve() != *this) return crypto::error("EC-point of wrong curve");
  if (!point.is_in_subgroup()) return crypto::error("EC-point is not on curve");

  if (!thread_local_store_allow_ecc_infinity) {
    if (point.is_infinity()) return crypto::error("EC-point is infinity");
  }
  return SUCCESS;
}
```

**File:** src/cbmpc/crypto/base_ecc_secp256k1.cpp (L271-277)
```cpp
error_t ecurve_secp256k1_t::from_bin(ecc_point_t& P, mem_t bin) const {
  secp256k1_ge ge;
  if (0 == secp256k1_eckey_pubkey_parse(&ge, bin.data, bin.size))
    return coinbase::error(E_CRYPTO, "secp256k1_eckey_pubkey_parse failed");
  secp256k1_gej_set_ge((secp256k1_gej*)P.secp256k1, &ge);
  return SUCCESS;
}
```
