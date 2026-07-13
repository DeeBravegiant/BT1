Looking at the ECDSA-2PC `detach_private_scalar` implementation and the Paillier serialization to confirm the exact root cause.### Title
ECDSA-2PC `detach_private_scalar` Retains Paillier Private Key in "Scalar-Removed" Blob, Enabling Full Private Scalar Recovery — (File: src/cbmpc/api/ecdsa2pc.cpp)

---

### Summary

`coinbase::api::ecdsa_2p::detach_private_scalar` (public C++ API) and its C-ABI wrapper `cbmpc_ecdsa_2p_detach_private_scalar` are designed to strip the private scalar from a P1 key blob for backup workflows. However, the produced "scalar-removed" blob still contains the full Paillier private key (`p`, `q`) and the Paillier ciphertext `c_key = Enc(x_share)`. Because `paillier.decrypt(c_key)` directly yields `x_share`, any party that obtains the "scalar-removed" blob can recover P1's private scalar without the separately returned `out_private_scalar` buffer, completely defeating the separation the API is meant to provide.

---

### Finding Description

**Key blob structure** (`src/cbmpc/api/ecdsa2pc.cpp` lines 21–32):

```cpp
struct key_blob_v1_t {
  uint32_t version = key_blob_version_v1;
  uint32_t role = 0;
  uint32_t curve = 0;
  buf_t Q_compressed;
  coinbase::crypto::bn_t x_share;
  coinbase::crypto::bn_t c_key;          // Paillier encryption of x_share
  coinbase::crypto::paillier_t paillier; // full key for P1 (has_private=true)
  void convert(coinbase::converter_t& c) {
    c.convert(version, role, curve, Q_compressed, x_share, c_key, paillier);
  }
};
```

**`detach_private_scalar` for ECDSA-2PC** (`src/cbmpc/api/ecdsa2pc.cpp` lines 208–234):

```cpp
out_private_scalar = key.x_share.to_bin();   // the "detached" scalar

key_blob_v1_t pub;
pub.x_share  = key.paillier.get_N().value(); // sentinel: out-of-range
pub.c_key    = key.c_key;                    // Enc(x_share) — kept intact
pub.paillier = key.paillier;                 // FULL Paillier key, incl. p, q
out_public_key_blob = coinbase::convert(pub);
```

**Paillier serialization** (`src/cbmpc/crypto/base_paillier.cpp` lines 15–21):

```cpp
void paillier_t::convert(coinbase::converter_t& converter) {
  converter.convert(has_private);
  converter.convert(N);
  if (has_private) {
    converter.convert(p);   // RSA prime p — serialized into the blob
    converter.convert(q);   // RSA prime q — serialized into the blob
  }
  ...
}
```

For P1 (`role == 0`), `has_private == true` is enforced by `blob_to_key` (line 50). Therefore the "scalar-removed" blob serializes `p` and `q` alongside `c_key`. The invariant confirmed at deserialization time (line 73–74) is:

```cpp
const coinbase::crypto::bn_t plain = blob.paillier.decrypt(blob.c_key);
if (plain != N.mod(blob.x_share)) return coinbase::error(E_FORMAT, "invalid key blob");
```

i.e., `c_key` is a valid Paillier encryption of `x_share mod N`. Because `x_share < N` by construction, `paillier.decrypt(c_key) == x_share`, and `x_share mod q_curve` is the ECDSA private scalar.

**Recovery path from the "scalar-removed" blob alone:**

1. Deserialize the blob; read `p`, `q`, `N`, `c_key`.
2. Reconstruct the Paillier private key from `(N, p, q)`.
3. Compute `x_share = paillier.decrypt(c_key)`.
4. Compute `x_scalar = x_share mod curve_order` — this is P1's ECDSA private scalar share.

No interaction with P2, no PVE ciphertext, and no `out_private_scalar` buffer is needed.

---

### Impact Explanation

The `detach_private_scalar` API is the library's documented mechanism for PVE-based backup (README lines 135–136, `include/cbmpc/api/ecdsa_2p.h` lines 54–65). Its contract is to produce a blob with "its private scalar removed." Callers following this contract will store the "scalar-removed" blob in ordinary persistent storage and protect only `out_private_scalar` via PVE or an HSM. Because the blob retains the Paillier private key and the ciphertext of the scalar, any storage-layer breach of the "scalar-removed" blob yields P1's full private scalar share — meeting the **Critical** impact bar: a shipped public API path lets an attacker recover a private scalar without the required honest participant.

With P1's scalar share `x1` and knowledge of the public key `Q = (x1+x2)*G`, an attacker can compute `x2 = (Q/G) - x1` (discrete-log is hard, but the attacker can sign unilaterally using `x1` alone in a forged 2PC session, or combine with a colluding/compromised P2).

---

### Likelihood Explanation

The API is part of the shipped public C++ and C ABIs (`include/cbmpc/api/ecdsa_2p.h`, `include/cbmpc/c_api/ecdsa_2p.h`). The README explicitly points integrators to `detach_private_scalar` for backup flows. The ECDSA-MP counterpart correctly documents "The public blob is safe to persist as public-only material"; the ECDSA-2PC counterpart makes no such claim, but the identical API shape and backup-workflow context create a strong expectation of equivalent safety. Any integrator who stores the "scalar-removed" blob in a database, object store, or backup system — a natural and expected usage — exposes the Paillier private key and therefore the private scalar.

---

### Recommendation

In `detach_private_scalar` for ECDSA-2PC, replace the full Paillier key copy with a public-only copy:

```cpp
// Instead of: pub.paillier = key.paillier;
paillier_t pub_paillier;
pub_paillier.create_pub(key.paillier.get_N().value()); // strips p, q
pub.paillier = pub_paillier;
```

This preserves `N` (needed for `verify_cipher` and `attach_private_scalar` validation) while removing `p` and `q`. The `c_key` field can remain — without the private key it is an opaque ciphertext that cannot be decrypted. Update the API documentation to explicitly state that the scalar-removed blob for P1 is safe to persist as public-only material only after this fix.

---

### Proof of Concept

```cpp
// 1. Run DKG as P1.
buf_t key_blob_p1;
coinbase::api::ecdsa_2p::dkg(job_p1, curve_id::secp256k1, key_blob_p1);

// 2. Call the public API to "detach" the scalar.
buf_t public_blob, x_scalar_out;
coinbase::api::ecdsa_2p::detach_private_scalar(key_blob_p1, public_blob, x_scalar_out);

// 3. Parse the "public" blob directly — no library API needed.
coinbase::api::ecdsa_2p::(anon)::key_blob_v1_t leaked;
coinbase::convert(leaked, public_blob);
// leaked.paillier now has has_private==true, p, q present.
// leaked.c_key == Enc(x_share) under that key.

// 4. Recover the private scalar.
const coinbase::crypto::bn_t x_share_recovered = leaked.paillier.decrypt(leaked.c_key);
// x_share_recovered == x_scalar_out (the "separately protected" scalar).

// 5. Verify.
const coinbase::crypto::mod_t& q = coinbase::crypto::curve_secp256k1.order();
assert(x_share_recovered % q == coinbase::crypto::bn_t::from_bin(x_scalar_out) % q);
```

The "scalar-removed" blob alone is sufficient to recover the private scalar; the PVE-protected `x_scalar_out` is redundant. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** src/cbmpc/api/ecdsa2pc.cpp (L21-32)
```cpp
struct key_blob_v1_t {
  uint32_t version = key_blob_version_v1;
  uint32_t role = 0;   // 0=p1, 1=p2
  uint32_t curve = 0;  // coinbase::api::curve_id

  buf_t Q_compressed;
  coinbase::crypto::bn_t x_share;
  coinbase::crypto::bn_t c_key;
  coinbase::crypto::paillier_t paillier;

  void convert(coinbase::converter_t& c) { c.convert(version, role, curve, Q_compressed, x_share, c_key, paillier); }
};
```

**File:** src/cbmpc/api/ecdsa2pc.cpp (L45-75)
```cpp
  // Defensive validation at the opaque blob boundary.
  //
  // In our ECDSA-2PC protocol, party P1 owns the Paillier private key, and `c_key` is an encryption of P1's share under
  // that key. Reject malformed / tampered blobs early.
  const bool paillier_has_private = blob.paillier.has_private_key();
  if ((blob.role == 0) != paillier_has_private) return coinbase::error(E_FORMAT, "invalid key blob");

  const auto& N = blob.paillier.get_N();
  if (N.value().get_bits_count() < coinbase::crypto::paillier_t::bit_size) {
    return coinbase::error(E_FORMAT, "invalid key blob");
  }

  // Intentionally do not enforce `x_share in [0, q)` here:
  // in ECDSA-2PC this share is maintained as a Paillier-compatible integer representative and can be
  // unreduced after refresh, so rejecting non-reduced values would break valid refreshed key blobs.
  //
  // However, `x_share` must remain in Z_N so that Paillier-related operations are well-defined and to avoid
  // attacker-controlled bignum blowups.
  if (!N.is_in_range(blob.x_share)) return coinbase::error(E_FORMAT, "invalid key blob");

  // Ensure `c_key` is a well-formed Paillier ciphertext under this key.
  {
    coinbase::crypto::vartime_scope_t vartime_scope;
    if (blob.paillier.verify_cipher(blob.c_key)) return coinbase::error(E_FORMAT, "invalid key blob");
  }

  // If we have the private key (P1), bind the share to its Paillier encryption.
  if (paillier_has_private) {
    const coinbase::crypto::bn_t plain = blob.paillier.decrypt(blob.c_key);
    if (plain != N.mod(blob.x_share)) return coinbase::error(E_FORMAT, "invalid key blob");
  }
```

**File:** src/cbmpc/api/ecdsa2pc.cpp (L208-234)
```cpp
error_t detach_private_scalar(mem_t key_blob, buf_t& out_public_key_blob, buf_t& out_private_scalar) {
  if (const error_t rv = coinbase::api::detail::validate_mem_arg_max_size(key_blob, "key_blob",
                                                                          coinbase::api::detail::MAX_OPAQUE_BLOB_SIZE))
    return rv;
  coinbase::mpc::ecdsa2pc::key_t key;
  error_t rv = deserialize_key_blob(key_blob, key);
  if (rv) return rv;

  curve_id cid;
  if (!from_internal_curve(key.curve, cid)) return coinbase::error(E_BADARG, "unsupported curve");
  if (cid == curve_id::ed25519) return coinbase::error(E_BADARG, "unsupported curve");

  // Variable-length big-endian encoding (may grow after refresh).
  out_private_scalar = key.x_share.to_bin();

  // Produce a v1-format blob with an invalid (out-of-range) scalar share so it is
  // rejected by sign/refresh APIs.
  key_blob_v1_t pub;
  pub.role = static_cast<uint32_t>(key.role);
  pub.curve = static_cast<uint32_t>(cid);
  pub.Q_compressed = key.Q.to_compressed_bin();
  pub.x_share = key.paillier.get_N().value();  // x_share == N is out of range
  pub.c_key = key.c_key;
  pub.paillier = key.paillier;
  out_public_key_blob = coinbase::convert(pub);
  return SUCCESS;
}
```

**File:** src/cbmpc/crypto/base_paillier.cpp (L15-40)
```cpp
void paillier_t::convert(coinbase::converter_t& converter) {
  converter.convert(has_private);
  converter.convert(N);
  if (has_private) {
    converter.convert(p);
    converter.convert(q);
  }

  if (!converter.is_write()) {
    if (converter.is_error()) return;
    if (N.get_bits_count() > bit_size) {
      converter.set_error();
      return;
    }
    if (has_private) {
      // This path rebuilds a private key from serialized state without semantically validating `p`/`q`.
      // It is intended for trusted local blobs.
      update_private();
    } else {
      // This path only rebuilds cached public state from the deserialized modulus.
      // `mod_t::convert()` already enforces the basic modulus representation, but callers are still responsible
      // for any higher-level/context-specific validation at the boundary after deserialization.
      update_public();
    }
  }
}
```

**File:** include/cbmpc/api/ecdsa_2p.h (L54-65)
```text
// Detach the private scalar share from a key blob, producing:
// - a key blob with its private scalar removed, and
// - the private scalar x encoded as a big-endian buffer.
//
// The scalar-removed blob is not usable for signing/refresh until restored with
// `attach_private_scalar`.
//
// Note (ECDSA-2PC encoding):
// - Unlike ECDSA-MP, this scalar encoding is NOT fixed-length. ECDSA-2PC keeps
//   the share as a Paillier-compatible integer representative and it may grow
//   after refresh.
error_t detach_private_scalar(mem_t key_blob, buf_t& out_public_key_blob, buf_t& out_private_scalar);
```

**File:** include/cbmpc/c_api/ecdsa_2p.h (L63-75)
```text
// Detach a key blob into a scalar-removed blob + private scalar.
//
// Notes:
// - Unlike ECDSA-MP, the scalar encoding is NOT fixed-length: after refresh,
//   ECDSA-2PC keeps the share as a Paillier-compatible integer representative and
//   it may grow.
//
// Ownership:
// - On success, `out_public_key_blob->data` and `out_private_scalar->data` are
//   allocated by the library and must be freed with `cbmpc_cmem_free(...)`.
// - On failure, outputs are set to `{NULL, 0}`.
cbmpc_error_t cbmpc_ecdsa_2p_detach_private_scalar(cmem_t key_blob, cmem_t* out_public_key_blob,
                                                   cmem_t* out_private_scalar);
```
