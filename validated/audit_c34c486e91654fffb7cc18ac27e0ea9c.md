### Title
Cross-Protocol Key Blob Type Confusion Between `ecdsa_mp` and `schnorr_mp` on secp256k1 — (File: `src/cbmpc/api/ecdsa_mp.cpp`, `src/cbmpc/api/schnorr_mp.cpp`)

### Summary
`coinbase::api::ecdsa_mp` and `coinbase::api::schnorr_mp` define structurally identical key blob formats with the same version constants and the same curve value for secp256k1. No protocol-type discriminant exists. A blob produced by one protocol is silently accepted as valid by the other, enabling cross-protocol key reuse through the public API.

### Finding Description

Both modules independently define a `key_blob_v1_t` with byte-for-byte identical serialization:

`src/cbmpc/api/ecdsa_mp.cpp`: [1](#0-0) 

`src/cbmpc/api/schnorr_mp.cpp`: [2](#0-1) 

Both use:
- `key_blob_version_v1 = 1` and `ac_key_blob_version_v1 = 2`
- `curve_id::secp256k1 = 2` as the stored curve value
- Identical field order: `version, curve, party_name, Q_compressed, Qis_compressed, x_share`

The `schnorr_mp` deserializer checks only `version == 1` and `curve == secp256k1`: [3](#0-2) 

The `ecdsa_mp` deserializer checks only `version == 1` and `curve != ed25519`: [4](#0-3) 

Neither check includes a protocol-type discriminant. The deserialization path uses `coinbase::convert`, which does **not** enforce strict trailing-byte rejection (only the `deser` helper does): [5](#0-4) 

Contrast with the strict `deser`: [6](#0-5) 

The same collision exists for the AC (version=2) blobs in both modules: [7](#0-6) [8](#0-7) 

A secondary, one-directional collision exists between `ecdsa_2p` and `schnorr_2p`: the `schnorr_2p` blob layout is a strict prefix of the `ecdsa_2p` layout (both version=1, curve=secp256k1; `ecdsa_2p` appends `c_key` and `paillier`). Because `coinbase::convert` ignores trailing bytes, an `ecdsa_2p` blob passes `schnorr_2p::deserialize_key_blob` without error: [9](#0-8) [10](#0-9) 

### Impact Explanation

An attacker-controlled blob (or a blob substituted via a compromised storage layer) is accepted under the wrong protocol. Concretely:

- An `ecdsa_mp` secp256k1 key blob passes every validation gate in `schnorr_mp::sign_additive` and `schnorr_mp::sign_ac`. The party produces a BIP340 Schnorr signature under a key share that was generated and intended exclusively for ECDSA. The resulting signature is cryptographically valid under the same secp256k1 public key.
- The reverse holds equally: a `schnorr_mp` blob passes `ecdsa_mp::sign_additive` and `ecdsa_mp::sign_ac`.
- For the 2PC case, an `ecdsa_2p` blob (secp256k1) passes `schnorr_2p::sign`, producing a BIP340 signature under the ECDSA party's share.

This violates key-separation: the same scalar share and public key are silently reused across ECDSA and Schnorr protocols. In blockchain contexts where the same secp256k1 key controls both legacy ECDSA outputs and Taproot/BIP340 outputs, cross-protocol signing under a substituted blob allows an adversary who controls blob storage to obtain signatures the key owner never intended to produce.

### Likelihood Explanation

The attacker must be able to supply or substitute the key blob passed to the signing API. The library documents blobs as opaque byte strings to be persisted by the caller; no out-of-band type tag is mandated. Any application that stores blobs in a shared database, passes them over an internal RPC, or accepts them from a partially-trusted component is exposed. The substitution requires no cryptographic capability — only the ability to swap one valid secp256k1 blob for another.

### Recommendation

Add a protocol-type discriminant field (e.g., `uint32_t protocol_id`) as the first serialized field in every key blob, with disjoint values across `ecdsa_mp`, `schnorr_mp`, `eddsa_mp`, `ecdsa_2p`, `schnorr_2p`, and `eddsa_2p`. Replace all `coinbase::convert(blob, in)` deserialization calls with the strict `coinbase::deser(in, blob)` form to reject trailing bytes and prevent prefix-aliasing between `ecdsa_2p` and `schnorr_2p` blobs.

### Proof of Concept

```
// Party A holds a legitimate ecdsa_mp secp256k1 key blob `ecdsa_blob`
// produced by coinbase::api::ecdsa_mp::dkg_additive(job, curve_id::secp256k1, ...).

// Attacker substitutes ecdsa_blob where a schnorr_mp blob is expected.
buf_t sig;
error_t rv = coinbase::api::schnorr_mp::sign_additive(
    job,
    mem_t(ecdsa_blob),   // ecdsa_mp blob passed to schnorr_mp API
    msg_hash,
    sig_receiver,
    sig);
// rv == SUCCESS
// sig contains a valid BIP340 Schnorr signature under the ECDSA public key.
// The ecdsa_mp key share was used for Schnorr signing without any error or warning.
```

The same substitution works in the reverse direction (`schnorr_mp` blob → `ecdsa_mp::sign_additive`) and for AC blobs (version=2). For the 2PC case, substitute an `ecdsa_2p` blob into `coinbase::api::schnorr_2p::sign`; the trailing `c_key`/`paillier` bytes are silently discarded by `coinbase::convert`.

### Citations

**File:** src/cbmpc/api/ecdsa_mp.cpp (L20-41)
```cpp
constexpr uint32_t key_blob_version_v1 = 1;
constexpr uint32_t ac_key_blob_version_v1 = 2;

using coinbase::api::detail::to_internal_curve;
using coinbase::api::detail::to_internal_job;
using coinbase::api::detail::validate_job_mp;

struct key_blob_v1_t {
  uint32_t version = key_blob_version_v1;
  uint32_t curve = 0;  // coinbase::api::curve_id

  std::string party_name;  // self identity (name-bound, not index-bound)

  buf_t Q_compressed;
  std::map<std::string, buf_t> Qis_compressed;  // name -> compressed Qi

  coinbase::crypto::bn_t x_share;

  void convert(coinbase::converter_t& c) {
    c.convert(version, curve, party_name, Q_compressed, Qis_compressed, x_share);
  }
};
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L136-153)
```cpp
  key_blob_v1_t blob;
  if (rv = coinbase::convert(blob, in)) return rv;
  if (blob.version != key_blob_version_v1) return coinbase::error(E_FORMAT, "unsupported key blob version");
  if (blob.party_name.empty()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.party_name != self_name) return coinbase::error(E_BADARG, "job.self mismatch key blob");
  if (job.party_names.size() != blob.Qis_compressed.size()) return coinbase::error(E_BADARG, "invalid key blob");

  // Ensure the party name set matches the job (order can differ).
  for (const auto& name_view : job.party_names) {
    const std::string name(name_view);
    if (blob.Qis_compressed.find(name) == blob.Qis_compressed.end())
      return coinbase::error(E_BADARG, "job.party_names mismatch key blob");
  }

  const auto cid = static_cast<curve_id>(blob.curve);
  if (cid == curve_id::ed25519) return coinbase::error(E_FORMAT, "invalid key blob curve");
  const auto curve = to_internal_curve(cid);
  if (!curve.valid()) return coinbase::error(E_FORMAT, "invalid key blob curve");
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L197-199)
```cpp
  key_blob_v1_t blob;
  if (rv = coinbase::convert(blob, in)) return rv;
  if (blob.version != ac_key_blob_version_v1) return coinbase::error(E_FORMAT, "unsupported key blob version");
```

**File:** src/cbmpc/api/schnorr_mp.cpp (L18-38)
```cpp
constexpr uint32_t key_blob_version_v1 = 1;
constexpr uint32_t ac_key_blob_version_v1 = 2;

using coinbase::api::detail::to_internal_job;
using coinbase::api::detail::validate_job_mp;

struct key_blob_v1_t {
  uint32_t version = key_blob_version_v1;
  uint32_t curve = 0;  // coinbase::api::curve_id

  std::string party_name;  // self identity (name-bound, not index-bound)

  buf_t Q_compressed;
  std::map<std::string, buf_t> Qis_compressed;  // name -> compressed Qi

  coinbase::crypto::bn_t x_share;

  void convert(coinbase::converter_t& c) {
    c.convert(version, curve, party_name, Q_compressed, Qis_compressed, x_share);
  }
};
```

**File:** src/cbmpc/api/schnorr_mp.cpp (L113-120)
```cpp
  key_blob_v1_t blob;
  if (rv = coinbase::convert(blob, in)) return rv;
  if (blob.version != key_blob_version_v1)
    return coinbase::error(E_FORMAT, "unsupported key blob version: " + std::to_string(blob.version));
  if (static_cast<curve_id>(blob.curve) != curve_id::secp256k1)
    return coinbase::error(E_FORMAT, "invalid key blob curve");
  if (blob.party_name.empty()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.party_name != self_name) return coinbase::error(E_BADARG, "job.self mismatch key blob");
```

**File:** src/cbmpc/api/schnorr_mp.cpp (L173-176)
```cpp
  key_blob_v1_t blob;
  if (rv = coinbase::convert(blob, in)) return rv;
  if (blob.version != ac_key_blob_version_v1) return coinbase::error(E_FORMAT, "unsupported key blob version");
  if (static_cast<curve_id>(blob.curve) != curve_id::secp256k1)
```

**File:** include-internal/cbmpc/internal/core/convert.h (L253-266)
```text
template <typename... ARGS>
error_t deser(mem_t bin, ARGS&... args) {
  converter_t converter(bin);
  converter.convert(args...);
  error_t rv = converter.get_rv();
  if (rv != SUCCESS) return rv;

  // Strict deserialization: reject trailing bytes
  if (converter.get_offset() != converter.get_size()) {
    return coinbase::error(E_BADARG);
  }

  return SUCCESS;
}
```

**File:** include-internal/cbmpc/internal/core/convert.h (L276-282)
```text
template <typename T>
error_t convert(T& dst, mem_t src) {
  if (src.size < 0 || (src.size && !src.data)) return coinbase::error(E_BADARG);
  converter_t converter(src);
  converter.convert(dst);
  return converter.get_rv();
}
```

**File:** src/cbmpc/api/schnorr2pc.cpp (L55-61)
```cpp
static error_t deserialize_key_blob(mem_t in, coinbase::mpc::schnorr2p::key_t& key) {
  key_blob_v1_t blob;
  const error_t rv = coinbase::convert(blob, in);
  if (rv) return rv;
  if (blob.version != key_blob_version_v1) return coinbase::error(E_FORMAT, "unsupported key blob version");
  return blob_to_key(blob, key);
}
```

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
