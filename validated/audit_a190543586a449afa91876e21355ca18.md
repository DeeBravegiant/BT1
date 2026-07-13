## Tracing the Vulnerability Claim

Let me trace the exact code path.

**`blob_to_keyset` in `src/cbmpc/api/hd_keyset_eddsa_2p.cpp`:** [1](#0-0) 

The function calls `from_bin` on `root_Q` and checks the return value, but never calls `curve.check()` or `is_in_subgroup()` on the decoded point.

**`ecurve_ed_t::from_bin` does NOT check subgroup membership:** [2](#0-1) 

It only verifies the point satisfies the curve equation. The underlying `ec25519_core::from_bin` decodes x,y coordinates and returns success for any valid curve point, including torsion points. [3](#0-2) 

**This is confirmed by the existing test suite:** [4](#0-3) 

`from_bin` returns `SUCCESS` for the order-2 torsion point, `is_on_curve()` is true, but `is_in_subgroup()` is false and `curve.check()` fails.

**`extract_root_public_key_compressed` returns the unvalidated point directly:** [5](#0-4) 

No subgroup check between `deserialize_keyset_blob` and `to_compressed_bin`.

**Contrast with other deserialization functions in the same codebase that DO perform the check:** [6](#0-5) [7](#0-6) 

Both call `curve.check()` (and `is_in_subgroup()`) after `from_bin`. `blob_to_keyset` in `hd_keyset_eddsa_2p.cpp` does neither.

---

## Assessment

The missing guard is real and confirmed. However, the **impact claim requires scrutiny**:

The claim states this causes `eddsa_2p::sign` signatures to fail external Ed25519 verification. But `extract_root_public_key_compressed` returns the **root** public key — signing uses **derived** keys produced by the interactive `derive_eddsa_2p_keys` protocol, not the root key directly. The root public key is informational (e.g., for BIP32 chain verification or publishing the HD root identity).

The concrete impact is narrower than claimed: a caller that receives the torsion bytes from `extract_root_public_key_compressed` and uses them as an Ed25519 public key for external verification will hold an invalid key (on the curve, not in the prime-order subgroup). Standard Ed25519 verifiers reject such keys. This is an accepted-bad-output path from an attacker-controlled blob — fitting the "High" scope criterion of attacker-controlled blobs being accepted with a point from the wrong subgroup.

The threat model is valid: `extract_root_public_key_compressed` is a public API that accepts arbitrary `mem_t keyset_blob` bytes. No authentication or provenance check is required to call it.

---

### Title
Missing prime-order subgroup check on `root_Q` in `blob_to_keyset` allows torsion point to be returned as root Ed25519 public key — (`src/cbmpc/api/hd_keyset_eddsa_2p.cpp`)

### Summary
`blob_to_keyset` calls `from_bin` on `root_Q_compressed` but omits the `curve.check()` / `is_in_subgroup()` guard present in every other Ed25519 blob deserializer in the codebase. A crafted keyset blob embedding a torsion point as `root_Q` passes deserialization and is returned verbatim by `extract_root_public_key_compressed`.

### Finding Description
In `blob_to_keyset` (`src/cbmpc/api/hd_keyset_eddsa_2p.cpp` lines 78–80), `keyset.root.Q.from_bin(keyset.curve, blob.root_Q_compressed)` succeeds for any valid curve point, including the 8 torsion points of Ed25519 (cofactor = 8). No subsequent `curve.check(keyset.root.Q)` or `keyset.root.Q.is_in_subgroup()` call is made. `extract_root_public_key_compressed` then calls `keyset.root.Q.to_compressed_bin()` and returns `SUCCESS` with the torsion encoding.

Contrast: `eddsa_mp.cpp` lines 197–198 and `eddsa2pc.cpp` lines 202–205 both call `curve.check()` (which rejects torsion points, as confirmed by `tests/unit/crypto/test_eddsa.cpp` lines 25–30) immediately after `from_bin`.

### Impact Explanation
A caller that passes a crafted blob and uses the returned 32 bytes as an Ed25519 public key holds a key that is on the curve but outside the prime-order subgroup. Standard Ed25519 implementations (RFC 8032 compliant) reject such keys during verification. Any signature presented against this key will fail, and the caller has no indication from the API that the returned key is invalid — `extract_root_public_key_compressed` returns `SUCCESS`.

### Likelihood Explanation
The function is a public, non-interactive API accepting arbitrary bytes. No privilege or protocol participation is required. Crafting a valid keyset blob with a known torsion point (e.g., the order-2 point `(0, p-1)` whose 32-byte encoding is `EC FF FF ... FF 7F`) requires only knowledge of the blob serialization format, which is straightforward to reverse from the `keyset_blob_v1_t` struct and `converter_t` usage.

### Recommendation
Add the following two checks in `blob_to_keyset` immediately after the `from_bin` calls, mirroring the pattern in `eddsa_mp.cpp`:

```cpp
error_t rv = keyset.root.Q.from_bin(keyset.curve, blob.root_Q_compressed);
if (rv) return rv;
if (keyset.curve.check(keyset.root.Q)) return coinbase::error(E_FORMAT, "invalid keyset blob root_Q");

rv = keyset.root.K.from_bin(keyset.curve, blob.root_K_compressed);
if (rv) return rv;
if (keyset.curve.check(keyset.root.K)) return coinbase::error(E_FORMAT, "invalid keyset blob root_K");
```

### Proof of Concept
1. Serialize a `keyset_blob_v1_t` with `version=1`, `role=0`, `curve=3` (ed25519), valid scalar shares in range, `root_Q_compressed` = `{0xEC, 0xFF, 0xFF, ...(29 bytes)..., 0xFF, 0x7F}` (the order-2 torsion point), and any valid 32-byte `root_K_compressed`.
2. Call `coinbase::api::hd_keyset_eddsa_2p::extract_root_public_key_compressed(crafted_blob, out)`.
3. Assert return value is `SUCCESS` and `out` equals the 32-byte torsion encoding.
4. Decode `out` with any RFC 8032 library and assert `is_in_subgroup()` is false.
5. Observe that passing `out` to any standard Ed25519 verifier as the public key causes all signature verifications to fail.

### Citations

**File:** src/cbmpc/api/hd_keyset_eddsa_2p.cpp (L63-81)
```cpp
static error_t blob_to_keyset(const keyset_blob_v1_t& blob, coinbase::mpc::key_share_eddsa_hdmpc_2p_t& keyset) {
  if (blob.role > 1) return coinbase::error(E_FORMAT, "invalid keyset blob role");
  if (static_cast<curve_id>(blob.curve) != curve_id::ed25519)
    return coinbase::error(E_FORMAT, "invalid keyset blob curve");

  keyset.party_index = static_cast<coinbase::mpc::party_idx_t>(blob.role);
  keyset.curve = coinbase::crypto::curve_ed25519;

  const coinbase::crypto::mod_t& q = keyset.curve.order();
  if (!q.is_in_range(blob.x_share)) return coinbase::error(E_FORMAT, "invalid keyset blob");
  if (!q.is_in_range(blob.k_share)) return coinbase::error(E_FORMAT, "invalid keyset blob");

  keyset.root.x_share = blob.x_share;
  keyset.root.k_share = blob.k_share;

  error_t rv = keyset.root.Q.from_bin(keyset.curve, blob.root_Q_compressed);
  if (rv) return rv;
  return keyset.root.K.from_bin(keyset.curve, blob.root_K_compressed);
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

**File:** src/cbmpc/crypto/base_eddsa.cpp (L167-174)
```cpp
error_t ecurve_ed_t::from_bin(ecc_point_t& P, mem_t bin) const {
  error_t rv = ec25519_core::from_bin(P.storage, bin);
  if (rv != 0) {
    set_infinity(P);
    return rv;
  }
  return SUCCESS;
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

**File:** tests/unit/crypto/test_eddsa.cpp (L25-30)
```cpp
  ecc_point_t P(curve);
  EXPECT_EQ(P.from_bin(curve, mem_t(order2, 32)), SUCCESS);
  EXPECT_TRUE(P.is_on_curve());
  EXPECT_FALSE(P.is_infinity());
  EXPECT_FALSE(P.is_in_subgroup());
  EXPECT_NE(curve.check(P), SUCCESS);
```

**File:** src/cbmpc/api/eddsa_mp.cpp (L196-199)
```cpp
  coinbase::crypto::ecc_point_t Q;
  if (rv = Q.from_bin(curve, blob.Q_compressed)) return coinbase::error(rv, "invalid key blob");
  if (curve.check(Q)) return coinbase::error(E_FORMAT, "invalid key blob");

```

**File:** src/cbmpc/api/eddsa2pc.cpp (L202-205)
```cpp
  if (rv = Qi_self.from_bin(curve, public_share_compressed))
    return coinbase::error(rv, "invalid public_share_compressed");
  if (rv = curve.check(Qi_self)) return coinbase::error(rv, "invalid public_share_compressed");
  if (!Qi_self.is_in_subgroup()) return coinbase::error(E_FORMAT, "invalid public_share_compressed");
```
