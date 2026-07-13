I have all the information needed. Let me trace the full call chain precisely.

### Title
Silent Hash Truncation in `curve_msg_to_bn` Enables Cross-Message Signature Validity - (`src/cbmpc/protocol/ecdsa_2p.cpp`, `include-internal/cbmpc/internal/protocol/util.h`)

---

### Summary

The public API `coinbase::api::ecdsa_2p::sign` accepts message digests up to 64 bytes (`MAX_MESSAGE_DIGEST_SIZE = 64`), but the internal signing path silently truncates any input longer than `curve.size()` (32 bytes for secp256k1/P-256) to exactly 32 bytes. The verification path (`ossl_ecdsa_verify`) applies the same truncation. As a result, a signature produced for message A is cryptographically valid for any message B that shares the same leading 32 bytes, regardless of what follows. This is a concrete, reachable signature forgery.

---

### Finding Description

**Entrypoint → truncation:**

`coinbase::api::ecdsa_2p::sign` calls `sign_common`, which validates `msg_hash` only against `MAX_MESSAGE_DIGEST_SIZE = 64` — it does not enforce an upper bound of `curve.size()`. [1](#0-0) [2](#0-1) 

The call then reaches `sign_batch_impl`, which converts each message to a scalar via `curve_msg_to_bn`: [3](#0-2) 

`curve_msg_to_bn` silently truncates any input longer than `curve.size()` (32 bytes for secp256k1): [4](#0-3) 

The truncated scalar `m[i]` is then used directly in the Paillier-based signing computation: [5](#0-4) 

**Internal verification also truncates:**

After producing the signature, `sign_batch_impl` verifies it by passing the *original* (untruncated) `msgs[i]` to `ecc_pub_key_t::verify`: [6](#0-5) 

`ecc_pub_key_t::verify` dispatches to `ossl_ecdsa_verify`, which applies the same truncation: [7](#0-6) 

Both signing and verification reduce any `>= 32`-byte input to its leading 32 bytes before computing the ECDSA scalar `e`. The two truncations are consistent, so the internal self-check always passes — but it also means the resulting signature is valid for *any* input sharing those 32 bytes.

**The forgery path:**

1. Caller submits `msg_hash_A` = `[b₀ … b₃₁ | X]` (33 bytes, accepted because `33 ≤ 64`).
2. `curve_msg_to_bn` produces `m = bn_t::from_bin([b₀ … b₃₁])`.
3. The MPC protocol signs `m`; the resulting DER signature `S` is returned.
4. `S` is also a valid ECDSA signature under the same key for `msg_hash_B` = `[b₀ … b₃₁ | Y]` for *any* byte `Y ≠ X`, because `ossl_ecdsa_verify` will compute the same `e` from both inputs.

The note about "identical signatures" in the question's proof idea is incorrect — fresh nonces `k1`, `k2` are sampled each session, so two signing calls produce different `(r, s)` pairs. The actual forgery is that **one signature is valid for multiple distinct messages**.

---

### Impact Explanation

An attacker who can obtain a legitimate signature for message A (e.g., by being one of the two MPC parties, or by observing a completed signing session) immediately possesses a valid signature for every message B that shares A's leading 32 bytes. This breaks existential unforgeability: the attacker can forge a signature for a message they never explicitly authorized. The impact is most acute when callers use SHA-384 (48 bytes) or SHA-512 (64 bytes) digests with secp256k1 or P-256 — both are within `MAX_MESSAGE_DIGEST_SIZE` and both are silently truncated.

---

### Likelihood Explanation

The API explicitly advertises support for 64-byte digests (`MAX_MESSAGE_DIGEST_SIZE = 64`, comment: "e.g., SHA-512 / SHA3-512"). A caller following that documentation and passing a SHA-512 hash over secp256k1 will unknowingly produce signatures that are valid for 2^256 distinct messages. No special privilege is required beyond being an API caller.

---

### Recommendation

Reject `msg_hash` inputs whose length exceeds `curve.size()` at the API boundary in `sign_common`, before the message is forwarded to the internal protocol. A one-line guard after the existing `validate_mem_arg_max_size` check suffices:

```cpp
// After deserializing the key and knowing the curve:
if ((int)msg_hash.size > key.curve.size())
    return coinbase::error(E_BADARG, "msg_hash too large for curve");
```

Alternatively, update `MAX_MESSAGE_DIGEST_SIZE` to equal `curve.size()` per curve, or document clearly that only the leading `curve.size()` bytes are bound to the signature and that callers must not pass longer inputs.

---

### Proof of Concept

```cpp
// Both parties hold the same key_blob_{1,2} from a prior DKG.
// msg_a and msg_b are 33-byte buffers identical in bytes 0-31, differing only in byte 32.
buf_t msg_a(33), msg_b(33);
for (int i = 0; i < 32; i++) msg_a[i] = msg_b[i] = static_cast<uint8_t>(i);
msg_a[32] = 0x00;
msg_b[32] = 0xFF;

buf_t sid1, sid2, sig;
// Sign msg_a via the 2PC protocol.
run_2pc(c1, c2,
    [&]{ return coinbase::api::ecdsa_2p::sign(job1, key_blob_1, msg_a, sid1, sig); },
    [&]{ return coinbase::api::ecdsa_2p::sign(job2, key_blob_2, msg_a, sid2, sig_unused); },
    rv1, rv2);
ASSERT_EQ(rv1, SUCCESS);

// Verify the signature produced for msg_a against msg_b — this must FAIL for a correct
// implementation but PASSES due to the truncation bug.
coinbase::crypto::ecc_pub_key_t pub(Q);
EXPECT_EQ(pub.verify(msg_b, sig), SUCCESS);  // demonstrates forgery
```

### Citations

**File:** src/cbmpc/api/mem_util.h (L18-18)
```text
inline constexpr int MAX_MESSAGE_DIGEST_SIZE = 64;                 // 64 bytes (e.g., SHA-512 / SHA3-512)
```

**File:** src/cbmpc/api/ecdsa2pc.cpp (L163-165)
```cpp
  if (const error_t rv = coinbase::api::detail::validate_mem_arg_max_size(
          msg_hash, "msg_hash", coinbase::api::detail::MAX_MESSAGE_DIGEST_SIZE))
    return rv;
```

**File:** src/cbmpc/protocol/ecdsa_2p.cpp (L254-257)
```cpp
  std::vector<bn_t> m(n_sigs);
  for (int i = 0; i < n_sigs; i++) {
    m[i] = curve_msg_to_bn(msgs[i], curve);
  }
```

**File:** src/cbmpc/protocol/ecdsa_2p.cpp (L332-333)
```cpp
      temp = k2_inv * m[i] + temp * r[i] + rho * q;
      auto c_tag = key.paillier.enc(temp, rc);
```

**File:** src/cbmpc/protocol/ecdsa_2p.cpp (L394-399)
```cpp
      crypto::ecc_pub_key_t ecc_verification_key(key.Q);
      if (rv = ecc_verification_key.verify(msgs[i], sigs[i]))
        if (global_abort_mode)
          return coinbase::error(E_ECDSA_2P_BIT_LEAK, "signature verification failed");
        else
          return coinbase::error(rv, "signature verification failed");
```

**File:** include-internal/cbmpc/internal/protocol/util.h (L68-71)
```text
inline bn_t curve_msg_to_bn(coinbase::mem_t msg, const ecurve_t& curve) {
  if (msg.size > curve.size()) msg.size = curve.size();
  return bn_t::from_bin(msg);
}
```

**File:** src/cbmpc/crypto/base_ecc.cpp (L109-111)
```cpp
  int curve_size = curve.size();
  if (hash.size >= curve_size) hash.size = curve_size;
  const bn_t e = bn_t::from_bin(hash);
```
