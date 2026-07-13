Now I have all the evidence needed. Let me compare the two deserialization paths directly.

### Title
Missing `curve.check()` on `root_Q` in `blob_to_keyset` allows torsion-point acceptance as root public key - (`src/cbmpc/api/hd_keyset_eddsa_2p.cpp`)

### Summary

`blob_to_keyset` in the EdDSA-2P HD keyset API deserializes `root_Q_compressed` using only `from_bin`, which validates that the bytes decode to a point on the Ed25519 curve but does **not** check prime-order subgroup membership. The sibling function `blob_to_key` in `eddsa2pc.cpp` explicitly calls `curve.check()` after `from_bin`. The missing guard means an attacker-controlled blob carrying a torsion-point encoding (e.g., the known order-2 point) passes deserialization and is returned verbatim by `extract_root_public_key_compressed` as the authoritative 32-byte root public key.

### Finding Description

**The gap — `blob_to_keyset` vs `blob_to_key`:**

`blob_to_key` in `src/cbmpc/api/eddsa2pc.cpp` (the flat EdDSA-2PC key path):

```cpp
error_t rv = key.Q.from_bin(key.curve, blob.Q_compressed);
if (rv) return coinbase::error(rv, "invalid key blob");
if (key.curve.check(key.Q)) return coinbase::error(E_FORMAT, "invalid key blob");  // ← subgroup check
``` [1](#0-0) 

`blob_to_keyset` in `src/cbmpc/api/hd_keyset_eddsa_2p.cpp` (the HD keyset path):

```cpp
error_t rv = keyset.root.Q.from_bin(keyset.curve, blob.root_Q_compressed);
if (rv) return rv;
return keyset.root.K.from_bin(keyset.curve, blob.root_K_compressed);
// ← NO curve.check() on Q or K
``` [2](#0-1) 

**Why `from_bin` alone is insufficient for Ed25519:**

The `ec25519_core::from_bin` implementation solves the curve equation `x² = (y²-1)/(dy²+1)` and returns SUCCESS for any valid encoding of a point on the curve — including torsion points outside the prime-order subgroup. [3](#0-2) 

`ecurve_t::check` additionally calls `is_in_subgroup()`, which for Ed25519 multiplies the point by `q-1` and checks `P == -P` (i.e., `q·P == ∞`). [4](#0-3) [5](#0-4) 

The existing test suite explicitly documents this distinction — `from_bin` succeeds for the order-2 torsion point `(x=0, y=-1)`, while `curve.check()` fails: [6](#0-5) 

**The reachable path:**

`extract_root_public_key_compressed` → `deserialize_keyset_blob` → `blob_to_keyset` → `from_bin` (succeeds) → returns `SUCCESS` with torsion-point bytes in `out_Q_compressed`. [7](#0-6) 

The same `deserialize_keyset_blob` is also called by `refresh` and `derive_eddsa_2p_keys`, so a torsion-point root Q propagates into those protocol paths as well. [8](#0-7) [9](#0-8) 

### Impact Explanation

The caller of `extract_root_public_key_compressed` receives a 32-byte encoding that it is documented to treat as the wallet root address (matching the encoding produced by `coinbase::api::eddsa_2p::get_public_key_compressed`). [10](#0-9) 

A torsion point returned here is cryptographically invalid as a wallet root: it is not in the prime-order subgroup, so any address or key derived from it does not correspond to a valid Ed25519 public key. The API accepts and surfaces this bad output with `SUCCESS`, giving the caller no indication that the root key is invalid. This matches the **High** impact category: attacker-controlled blob data is accepted and returned as authoritative cryptographic output.

### Likelihood Explanation

The attacker must supply a crafted blob to the API. The blob format is documented as an opaque byte string that callers persist and pass back. Any caller that accepts a blob from an untrusted source (e.g., a counterparty, a storage layer, or a network peer) and passes it to `extract_root_public_key_compressed` (or `refresh`/`derive_eddsa_2p_keys`) is exposed. The torsion-point bytes are publicly known constants, making crafting trivial.

### Recommendation

Add `curve.check()` calls for both `root.Q` and `root.K` in `blob_to_keyset` in `src/cbmpc/api/hd_keyset_eddsa_2p.cpp`, mirroring the pattern already used in `blob_to_key`:

```cpp
error_t rv = keyset.root.Q.from_bin(keyset.curve, blob.root_Q_compressed);
if (rv) return rv;
if (keyset.curve.check(keyset.root.Q)) return coinbase::error(E_FORMAT, "invalid keyset blob");

rv = keyset.root.K.from_bin(keyset.curve, blob.root_K_compressed);
if (rv) return rv;
if (keyset.curve.check(keyset.root.K)) return coinbase::error(E_FORMAT, "invalid keyset blob");
```

### Proof of Concept

```cpp
// Known Ed25519 order-2 torsion point (x=0, y=-1), on-curve but not in prime-order subgroup.
// Confirmed by test CryptoEdDSA::RejectTorsionAndFixInfinityEq:
//   from_bin() returns SUCCESS, curve.check() returns non-SUCCESS.
uint8_t torsion[32];
torsion[0] = 0xec;
for (int i = 1; i < 31; i++) torsion[i] = 0xff;
torsion[31] = 0x7f;

// Build a minimal keyset_blob_v1_t with root_Q_compressed = torsion point.
// (version=1, role=0, curve=ed25519, root_Q_compressed=torsion, root_K_compressed=<valid subgroup point>,
//  x_share=1, k_share=1)
buf_t crafted_blob = build_keyset_blob(torsion, ...);

buf_t out;
error_t rv = coinbase::api::hd_keyset_eddsa_2p::extract_root_public_key_compressed(crafted_blob, out);
assert(rv == SUCCESS);                    // passes — no curve.check() in blob_to_keyset
assert(out == buf_t(torsion, 32));        // torsion point returned as root public key

// Confirm the returned point fails subgroup check:
ecc_point_t Q(coinbase::crypto::curve_ed25519);
Q.from_bin(coinbase::crypto::curve_ed25519, out);
assert(coinbase::crypto::curve_ed25519.check(Q) != SUCCESS);  // not in prime-order subgroup
```

### Citations

**File:** src/cbmpc/api/eddsa2pc.cpp (L40-42)
```cpp
  error_t rv = key.Q.from_bin(key.curve, blob.Q_compressed);
  if (rv) return coinbase::error(rv, "invalid key blob");
  if (key.curve.check(key.Q)) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/api/hd_keyset_eddsa_2p.cpp (L78-80)
```cpp
  error_t rv = keyset.root.Q.from_bin(keyset.curve, blob.root_Q_compressed);
  if (rv) return rv;
  return keyset.root.K.from_bin(keyset.curve, blob.root_K_compressed);
```

**File:** src/cbmpc/api/hd_keyset_eddsa_2p.cpp (L121-141)
```cpp
error_t refresh(const coinbase::api::job_2p_t& job, mem_t keyset_blob, buf_t& new_keyset_blob) {
  if (const error_t rv = validate_job_2p(job)) return rv;
  if (const error_t rv = coinbase::api::detail::validate_mem_arg_max_size(keyset_blob, "keyset_blob",
                                                                          coinbase::api::detail::MAX_OPAQUE_BLOB_SIZE))
    return rv;
  coinbase::mpc::key_share_eddsa_hdmpc_2p_t keyset;
  error_t rv = deserialize_keyset_blob(keyset_blob, keyset);
  if (rv) return rv;

  const auto self = to_internal_party(job.self);
  if (static_cast<uint32_t>(keyset.party_index) != static_cast<uint32_t>(self))
    return coinbase::error(E_BADARG, "job.self mismatch keyset blob role");

  coinbase::mpc::job_2p_t mpc_job = to_internal_job(job);

  coinbase::mpc::key_share_eddsa_hdmpc_2p_t new_keyset;
  rv = coinbase::mpc::key_share_eddsa_hdmpc_2p_t::refresh(mpc_job, keyset, new_keyset);
  if (rv) return rv;

  return serialize_keyset_blob(new_keyset, new_keyset_blob);
}
```

**File:** src/cbmpc/api/hd_keyset_eddsa_2p.cpp (L143-188)
```cpp
error_t derive_eddsa_2p_keys(const coinbase::api::job_2p_t& job, mem_t keyset_blob, const bip32_path_t& hardened_path,
                             const std::vector<bip32_path_t>& non_hardened_paths, buf_t& sid,
                             std::vector<buf_t>& out_eddsa_2p_key_blobs) {
  if (const error_t rv = validate_job_2p(job)) return rv;
  if (const error_t rv = coinbase::api::detail::validate_mem_arg_max_size(keyset_blob, "keyset_blob",
                                                                          coinbase::api::detail::MAX_OPAQUE_BLOB_SIZE))
    return rv;
  coinbase::mpc::key_share_eddsa_hdmpc_2p_t keyset;
  error_t rv = deserialize_keyset_blob(keyset_blob, keyset);
  if (rv) return rv;

  const auto self = to_internal_party(job.self);
  if (static_cast<uint32_t>(keyset.party_index) != static_cast<uint32_t>(self))
    return coinbase::error(E_BADARG, "job.self mismatch keyset blob role");

  rv = validate_no_duplicate_bip32_paths(non_hardened_paths);
  if (rv) return rv;

  coinbase::mpc::job_2p_t mpc_job = to_internal_job(job);

  const coinbase::mpc::bip32_path_t hardened_path_internal = to_internal_bip32_path(hardened_path);
  std::vector<coinbase::mpc::bip32_path_t> non_hardened_paths_internal;
  non_hardened_paths_internal.reserve(non_hardened_paths.size());
  for (const auto& p : non_hardened_paths) non_hardened_paths_internal.push_back(to_internal_bip32_path(p));

  std::vector<coinbase::mpc::eddsa2pc::key_t> derived_keys(non_hardened_paths.size());
  rv = coinbase::mpc::key_share_eddsa_hdmpc_2p_t::derive_keys(mpc_job, keyset, hardened_path_internal,
                                                              non_hardened_paths_internal, sid, derived_keys);
  if (rv) {
    out_eddsa_2p_key_blobs.clear();
    return rv;
  }

  std::vector<buf_t> blobs;
  blobs.resize(derived_keys.size());
  for (size_t i = 0; i < derived_keys.size(); i++) {
    rv = serialize_eddsa2pc_key_blob(derived_keys[i], blobs[i]);
    if (rv) {
      out_eddsa_2p_key_blobs.clear();
      return rv;
    }
  }

  out_eddsa_2p_key_blobs = std::move(blobs);
  return SUCCESS;
}
```

**File:** src/cbmpc/api/hd_keyset_eddsa_2p.cpp (L190-199)
```cpp
error_t extract_root_public_key_compressed(mem_t keyset_blob, buf_t& out_Q_compressed) {
  if (const error_t rv = coinbase::api::detail::validate_mem_arg_max_size(keyset_blob, "keyset_blob",
                                                                          coinbase::api::detail::MAX_OPAQUE_BLOB_SIZE))
    return rv;
  coinbase::mpc::key_share_eddsa_hdmpc_2p_t keyset;
  const error_t rv = deserialize_keyset_blob(keyset_blob, keyset);
  if (rv) return rv;
  out_Q_compressed = keyset.root.Q.to_compressed_bin();
  return SUCCESS;
}
```

**File:** src/cbmpc/crypto/ec25519_core.cpp (L865-870)
```cpp
bool is_in_subgroup(const crypto::ecp_storage_t* a) {
  static bn_t q_minus_1 = bn_t::from_hex("1000000000000000000000000000000014DEF9DEA2F79CD65812631A5CF5D3EC");
  point_t x;
  curve_t::mul(*(const point_t*)a, q_minus_1, x);
  return *(const point_t*)a == -x;
}
```

**File:** src/cbmpc/crypto/ec25519_core.cpp (L872-912)
```cpp
static error_t from_bin(point_t& R, mem_t bin) {
  if (bin.size != 32) return coinbase::error(E_FORMAT);

  buf_t buf = bin.rev();
  uint8_t neg = buf[0] >> 7;
  buf[0] &= 0x7f;
  fe_t y = fe_t::to_fe(uint256_t::from_bin(buf));

  // x² = (y² - 1) / (dy² + 1)

  fe_t u, v, w, vxx, check;

  u = y * y;
  v = u * formula_t::get_d();
  u -= fe_t::one();       // u = y^2-1
  v += fe_t::one();       // v = dy^2+1
  w = u * v;              // w = u*v
  fe_t x = w.pow22523();  // x = w^((q-5)/8)
  x *= u;                 // x = u * w^((q-5)/8)

  vxx = x * x;
  vxx *= v;
  check = vxx - u;  // vx^2-u
  if (!check.is_zero()) {
    check = vxx + u;  // vx^2+u
    if (!check.is_zero()) {
      return coinbase::error(E_CRYPTO);
    }
    static const fe_t sqrtm1 =
        fe_t::from_bn(bn_t::from_hex("2b8324804fc1df0b2b4d00993dfbd7a72f431806ad2fe478c4ee1b274a0ea0b0"));
    x *= sqrtm1;
  }

  uint256_t x_val = x.from_fe();
  if (neg != (x_val.w0 & 1)) x = -x;

  R.x = x;
  R.y = y;
  R.z = fe_t::one();
  return 0;
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

**File:** tests/unit/crypto/test_eddsa.cpp (L14-30)
```cpp
TEST(CryptoEdDSA, RejectTorsionAndFixInfinityEq) {
  crypto::vartime_scope_t vartime_scope;
  ecurve_t curve = crypto::curve_ed25519;

  // Compressed encoding of the Ed25519 order-2 point (x=0, y=-1):
  // y = p-1 = 2^255-20, sign bit = 0.
  uint8_t order2[32];
  order2[0] = 0xec;
  for (int i = 1; i < 31; i++) order2[i] = 0xff;
  order2[31] = 0x7f;

  ecc_point_t P(curve);
  EXPECT_EQ(P.from_bin(curve, mem_t(order2, 32)), SUCCESS);
  EXPECT_TRUE(P.is_on_curve());
  EXPECT_FALSE(P.is_infinity());
  EXPECT_FALSE(P.is_in_subgroup());
  EXPECT_NE(curve.check(P), SUCCESS);
```

**File:** include/cbmpc/api/hd_keyset_eddsa_2p.h (L40-44)
```text
// Extract the compressed root public key Q from a keyset blob.
//
// Output encoding matches `coinbase::api::eddsa_2p::get_public_key_compressed`:
// a 32-byte Ed25519 compressed public key.
error_t extract_root_public_key_compressed(mem_t keyset_blob, buf_t& out_Q_compressed);
```
