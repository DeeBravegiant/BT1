### Title
Missing Protocol-Type Discriminator in MP Key Blobs Allows Cross-Protocol Key Blob Substitution — (`src/cbmpc/api/ecdsa_mp.cpp`, `src/cbmpc/api/schnorr_mp.cpp`)

---

### Summary

The additive key blob format for `ecdsa_mp` (secp256k1) and `schnorr_mp` (secp256k1) are byte-for-byte identical serializations with the same version tag (`1`) and no protocol-type field. Both deserializers accept each other's blobs without error. A key blob produced by `ecdsa_mp::dkg_additive()` is silently accepted by `schnorr_mp::sign_additive()`, and vice versa, through the shipped public C++ and C APIs.

---

### Finding Description

`src/cbmpc/api/ecdsa_mp.cpp` defines:

```cpp
constexpr uint32_t key_blob_version_v1 = 1;

struct key_blob_v1_t {
  uint32_t version = key_blob_version_v1;
  uint32_t curve = 0;          // coinbase::api::curve_id
  std::string party_name;
  buf_t Q_compressed;
  std::map<std::string, buf_t> Qis_compressed;
  coinbase::crypto::bn_t x_share;
  void convert(coinbase::converter_t& c) {
    c.convert(version, curve, party_name, Q_compressed, Qis_compressed, x_share);
  }
};
```

`src/cbmpc/api/schnorr_mp.cpp` defines an **identical** struct with the **identical** `convert` body and the **identical** `key_blob_version_v1 = 1`.

The ECDSA-MP additive deserializer (`deserialize_key_blob`, lines 129–187) accepts any blob where `version == 1` and `curve != ed25519`. The Schnorr-MP additive deserializer (`deserialize_key_blob`, lines 87–163) accepts any blob where `version == 1` and `curve == secp256k1`. Both also verify `x_share * G == Qi_self` and party-name consistency — checks that a legitimately generated blob from the other protocol satisfies trivially, since both protocols use additive secp256k1 shares.

There is no `protocol_id` field, no namespace tag, and no other discriminator in the serialized wire format.

---

### Impact Explanation

Because the two blob types are structurally indistinguishable:

1. **Cross-protocol signing succeeds silently.** Passing a Schnorr-MP key blob to `coinbase::api::ecdsa_mp::sign_additive()` (or `cbmpc_ecdsa_mp_sign_additive` in the C API) returns `SUCCESS` and produces a valid ECDSA signature under the Schnorr public key. The inverse holds for `schnorr_mp::sign_additive()` with an ECDSA-MP blob.

2. **Key-share material is consumed under the wrong protocol.** The ECDSA-MP signing protocol (OT-based, Paillier-assisted) and the Schnorr-MP signing protocol have different security proofs and different transcript structures. Running one protocol's signing logic against the other protocol's key shares violates the binding between the key-generation transcript and the signing transcript. This is the direct analog of the ChainID=1 replay: the same scalar material is accepted under a different protocol context.

3. **Reachable through the shipped public API.** Both `coinbase::api::ecdsa_mp::sign_additive` and `coinbase::api::schnorr_mp::sign_additive` are exported in `include/cbmpc/api/ecdsa_mp.h` and `include/cbmpc/api/schnorr_mp.h`, and wrapped in the stable C ABI (`cbmpc_ecdsa_mp_sign_additive`, `cbmpc_schnorr_mp_sign_additive`). Any caller that holds a blob from one DKG can pass it to the other signing function.

---

### Likelihood Explanation

The scenario is reachable without any threshold collusion or transport compromise. A single party that holds both an ECDSA-MP blob and a Schnorr-MP blob (e.g., from two separate DKG sessions over the same party set) can substitute one for the other in any signing call. An application that accidentally stores blobs under a shared key-value store keyed only by party name — a natural implementation choice given that the blobs are described as "opaque" — will silently use the wrong blob. The library provides no runtime signal that the wrong protocol's blob was supplied.

---

### Recommendation

Add a `protocol_id` field (e.g., an enum: `ecdsa_mp_additive = 1`, `schnorr_mp_additive = 2`, `ecdsa_mp_ac = 3`, …) as the first field in every key blob's `convert` body, and reject blobs whose `protocol_id` does not match the calling API. This is the direct fix analogous to assigning a unique ChainID: it makes blobs from different protocol contexts non-interchangeable at the deserialization boundary.

---

### Proof of Concept

```
// Step 1: run ecdsa_mp DKG on secp256k1 → obtain ecdsa_blob_p0, ecdsa_blob_p1
coinbase::api::ecdsa_mp::dkg_additive(job_p0, curve_id::secp256k1, ecdsa_blob_p0, sid);
coinbase::api::ecdsa_mp::dkg_additive(job_p1, curve_id::secp256k1, ecdsa_blob_p1, sid);

// Step 2: pass the ECDSA-MP blobs directly to schnorr_mp::sign_additive
// Both deserializers accept version=1, curve=secp256k1, additive shares.
// The call returns SUCCESS and emits a BIP340 Schnorr signature
// under the ECDSA-MP public key — wrong protocol, wrong key context,
// no error returned.
coinbase::api::schnorr_mp::sign_additive(job_p0, ecdsa_blob_p0, msg32, 0, sig);
coinbase::api::schnorr_mp::sign_additive(job_p1, ecdsa_blob_p1, msg32, 0, sig);
// sig is a valid Schnorr signature under the ECDSA-MP public key.
```

The root cause is that `key_blob_version_v1 = 1` is shared across both protocols with no additional discriminator, exactly mirroring the ChainID=1 collision: the same numeric identifier is reused across two distinct protocol contexts, and the deserializer has no way to reject a blob that was legitimately produced by the other context. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** src/cbmpc/api/ecdsa_mp.cpp (L129-187)
```cpp
static error_t deserialize_key_blob(const coinbase::api::job_mp_t& job, mem_t in, coinbase::mpc::ecdsampc::key_t& key) {
  error_t rv = UNINITIALIZED_ERROR;

  if (job.self < 0 || static_cast<size_t>(job.self) >= job.party_names.size())
    return coinbase::error(E_BADARG, "invalid job.self");
  const std::string self_name(job.party_names[static_cast<size_t>(job.self)]);

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

  const coinbase::crypto::mod_t& q = curve.order();
  if (!q.is_in_range(blob.x_share)) return coinbase::error(E_FORMAT, "invalid key blob");

  coinbase::crypto::ecc_point_t Q;
  if (rv = Q.from_bin(curve, blob.Q_compressed)) return coinbase::error(rv, "invalid key blob");

  coinbase::crypto::ss::party_map_t<coinbase::crypto::ecc_point_t> Qis;
  for (const auto& name_view : job.party_names) {
    const std::string name(name_view);
    const auto it = blob.Qis_compressed.find(name);
    if (it == blob.Qis_compressed.end()) return coinbase::error(E_BADARG, "job.party_names mismatch key blob");

    coinbase::crypto::ecc_point_t Qi;
    if (rv = Qi.from_bin(curve, it->second)) return coinbase::error(rv, "invalid key blob");
    Qis[name] = std::move(Qi);
  }

  coinbase::crypto::ecc_point_t Q_sum = curve.infinity();
  for (const auto& kv : Qis) Q_sum += kv.second;
  if (Q != Q_sum) return coinbase::error(E_FORMAT, "invalid key blob");

  const auto& G = curve.generator();
  const auto it_self = Qis.find(blob.party_name);
  if (it_self == Qis.end()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.x_share * G != it_self->second) return coinbase::error(E_FORMAT, "invalid key blob");

  key.party_name = blob.party_name;
  key.curve = curve;
  key.x_share = blob.x_share;
  key.Qis = std::move(Qis);
  key.Q = std::move(Q);
  return SUCCESS;
}
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

**File:** src/cbmpc/api/schnorr_mp.cpp (L87-163)
```cpp
static error_t serialize_key_blob(const coinbase::api::job_mp_t& job, const coinbase::mpc::schnorrmp::key_t& key,
                                  buf_t& out) {
  if (job.self < 0 || static_cast<size_t>(job.self) >= job.party_names.size())
    return coinbase::error(E_BADARG, "invalid job.self");

  const std::string self_name(job.party_names[static_cast<size_t>(job.self)]);
  return serialize_key_blob_for_party_names(job.party_names, self_name, key, key_blob_version_v1, out);
}

static error_t serialize_ac_key_blob(const coinbase::api::job_mp_t& job, const coinbase::mpc::schnorrmp::key_t& key,
                                     buf_t& out) {
  if (job.self < 0 || static_cast<size_t>(job.self) >= job.party_names.size())
    return coinbase::error(E_BADARG, "invalid job.self");

  const std::string self_name(job.party_names[static_cast<size_t>(job.self)]);
  return serialize_key_blob_for_party_names(job.party_names, self_name, key, ac_key_blob_version_v1, out);
}

static error_t deserialize_key_blob(const coinbase::api::job_mp_t& job, mem_t in,
                                    coinbase::mpc::schnorrmp::key_t& key) {
  error_t rv = UNINITIALIZED_ERROR;

  if (job.self < 0 || static_cast<size_t>(job.self) >= job.party_names.size())
    return coinbase::error(E_BADARG, "invalid job.self");
  const std::string self_name(job.party_names[static_cast<size_t>(job.self)]);

  key_blob_v1_t blob;
  if (rv = coinbase::convert(blob, in)) return rv;
  if (blob.version != key_blob_version_v1)
    return coinbase::error(E_FORMAT, "unsupported key blob version: " + std::to_string(blob.version));
  if (static_cast<curve_id>(blob.curve) != curve_id::secp256k1)
    return coinbase::error(E_FORMAT, "invalid key blob curve");
  if (blob.party_name.empty()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.party_name != self_name) return coinbase::error(E_BADARG, "job.self mismatch key blob");
  if (blob.Qis_compressed.size() != job.party_names.size()) return coinbase::error(E_BADARG, "invalid key blob");

  // Ensure the party name set matches the job (order can differ).
  for (const auto& name_view : job.party_names) {
    const std::string name(name_view);
    if (blob.Qis_compressed.find(name) == blob.Qis_compressed.end())
      return coinbase::error(E_BADARG, "job.party_names mismatch key blob");
  }

  const auto curve = coinbase::crypto::curve_secp256k1;
  const coinbase::crypto::mod_t& q = curve.order();
  if (!q.is_in_range(blob.x_share)) return coinbase::error(E_FORMAT, "invalid key blob");

  coinbase::crypto::ecc_point_t Q;
  if (rv = Q.from_bin(curve, blob.Q_compressed)) return coinbase::error(rv, "invalid key blob");

  coinbase::crypto::ss::party_map_t<coinbase::crypto::ecc_point_t> Qis;
  for (const auto& name_view : job.party_names) {
    const std::string name(name_view);
    const auto it = blob.Qis_compressed.find(name);
    if (it == blob.Qis_compressed.end()) return coinbase::error(E_BADARG, "job.party_names mismatch key blob");

    coinbase::crypto::ecc_point_t Qi;
    if (rv = Qi.from_bin(curve, it->second)) return coinbase::error(rv, "invalid key blob");
    Qis[name] = std::move(Qi);
  }

  coinbase::crypto::ecc_point_t Q_sum = curve.infinity();
  for (const auto& kv : Qis) Q_sum += kv.second;
  if (Q != Q_sum) return coinbase::error(E_FORMAT, "invalid key blob");

  const auto& G = curve.generator();
  const auto it_self = Qis.find(blob.party_name);
  if (it_self == Qis.end()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.x_share * G != it_self->second) return coinbase::error(E_FORMAT, "invalid key blob");

  key.party_name = blob.party_name;
  key.curve = curve;
  key.x_share = blob.x_share;
  key.Qis = std::move(Qis);
  key.Q = std::move(Q);
  return SUCCESS;
}
```

**File:** include/cbmpc/api/ecdsa_mp.h (L63-63)
```text
error_t sign_additive(const job_mp_t& job, mem_t key_blob, mem_t msg, party_idx_t sig_receiver, buf_t& sig_der);
```

**File:** include/cbmpc/api/schnorr_mp.h (L72-73)
```text
error_t sign_additive(const coinbase::api::job_mp_t& job, mem_t key_blob, mem_t msg, party_idx_t sig_receiver,
                      buf_t& sig);
```
