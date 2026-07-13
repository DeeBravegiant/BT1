### Title
Floor Division in `bits_to_bytes_floor` Silently Truncates Refresh Randomness, Reducing Statistical Security Below the Intended Parameter — (File: src/cbmpc/protocol/hd_keyset_eddsa_2p.cpp)

---

### Summary

In `key_share_eddsa_hdmpc_2p_t::refresh`, the helper `bits_to_bytes_floor` (a strict floor/truncating division `bits >> 3`) is used to size the agreed-random buffer that seeds the key-share re-randomisation masks `r_x` and `r_k`. For any curve whose order bit-length plus `SEC_P_STAT` is not a multiple of 8 — most notably Ed25519 (253-bit order) — the floor division silently discards up to 7 bits, so the masks are drawn from a distribution with measurably less than the intended 2⁶⁴ bits of statistical security. The correct helper, `bits_to_bytes` (ceiling division), is available in the same header and is used everywhere else in the codebase for this purpose.

---

### Finding Description

**Root cause — the floor division**

`bits_to_bytes_floor` is defined as:

```cpp
// include-internal/cbmpc/internal/core/utils.h:23-26
inline int bits_to_bytes_floor(int bits) {
  cb_assert(bits >= 0);
  return bits >> 3;          // ← strict floor: drops the remainder
}
```

The ceiling variant used everywhere else is:

```cpp
// include-internal/cbmpc/internal/core/utils.h:27-30
inline int bits_to_bytes(int bits) {
  cb_assert(bits >= 0);
  return (bits + 7) >> 3;    // ← ceiling
}
```

**The affected call site**

```cpp
// src/cbmpc/protocol/hd_keyset_eddsa_2p.cpp:66-71
int rand_bitlen = q.get_bits_count() + SEC_P_STAT;   // intended: |q| + 64 bits
int rand_size   = bits_to_bytes_floor(rand_bitlen);  // ← FLOOR, not ceiling
if (rv = agree_random(job, 2 * rand_bitlen, rand)) return rv;

bn_t r_x = bn_t::from_bin(rand.take(rand_size)) % q;
bn_t r_k = bn_t::from_bin(rand.skip(rand_size).take(rand_size)) % q;
```

`agree_random` correctly generates `2 × rand_bitlen` bits. However, `rand_size` is computed with floor division, so each mask only consumes `bits_to_bytes_floor(rand_bitlen)` bytes — potentially one byte (8 bits) fewer than needed.

**Concrete numbers for Ed25519**

| Quantity | Value |
|---|---|
| `q.get_bits_count()` (Ed25519 order) | 253 bits |
| `SEC_P_STAT` | 64 bits |
| `rand_bitlen` | 317 bits |
| `bits_to_bytes_floor(317)` | `317 >> 3 = 39` bytes = **312 bits** |
| `bits_to_bytes(317)` (correct) | `(317+7) >> 3 = 40` bytes = 320 bits |
| Bits actually fed into `% q` | 312 |
| Statistical distance from uniform mod q | 2^(−(312−253)) = **2^(−59)** |
| Intended statistical distance | 2^(−64) |
| Security bits lost | **5** |

For secp256k1 / P-256 (256-bit order): `256 + 64 = 320`, which is exactly divisible by 8, so no bits are lost on those curves. The bug is curve-specific and silently active for Ed25519.

**Why `agree_random` does not compensate**

`agree_random` is called with `2 * rand_bitlen = 634` bits, producing 80 bytes. Only `2 × 39 = 78` bytes are consumed; the last 2 bytes are silently discarded. The protocol never detects the shortfall.

---

### Impact Explanation

The `refresh` protocol is the mechanism that provides forward secrecy for EdDSA 2P HD key shares: after refresh, a party that previously held the old shares should learn nothing about the new ones. This guarantee rests on `r_x` and `r_k` being statistically indistinguishable from uniform mod q. With 312 bits of input instead of 317, the statistical distance from uniform is 2^(−59) rather than the library's stated 2^(−64). An adversary with 2^59 work can distinguish the refresh masks from uniform, which in principle allows partial information about the refreshed key shares to be recovered. The Full (Internal) API path `coinbase::mpc::key_share_eddsa_hdmpc_2p_t::refresh` is the entry point; it is documented as a supported protocol function for developers building on the library.

---

### Likelihood Explanation

The bug is always present for Ed25519 key refresh — no special input is required. Any caller of `refresh` on an Ed25519 HD key set triggers it. The condition `(q.get_bits_count() + SEC_P_STAT) % 8 != 0` evaluates to true for Ed25519 (253 + 64 = 317, remainder 5) and for P-521 (521 + 64 = 585, remainder 1), but not for secp256k1 or P-256.

---

### Recommendation

Replace `bits_to_bytes_floor` with `bits_to_bytes` (ceiling division) at the affected call site:

```cpp
// src/cbmpc/protocol/hd_keyset_eddsa_2p.cpp:67
int rand_size = bits_to_bytes(rand_bitlen);   // was: bits_to_bytes_floor
```

This is consistent with every other location in the codebase that converts a desired bit-length to a byte buffer size for cryptographic sampling (e.g., `drbg_sample_string`, `gen_random_bitlen`, `hash_string_t::bitlen`).

---

### Proof of Concept

```
Ed25519 order q: 2^252 + 27742317777372353535851937790883648493
q.get_bits_count() = 253
SEC_P_STAT         = 64
rand_bitlen        = 317

bits_to_bytes_floor(317) = 317 >> 3 = 39   (312 bits)
bits_to_bytes(317)       = (317+7)>>3 = 40  (320 bits)

r_x drawn from 312-bit uniform → statistical distance from U(Z_q):
  2^312 / q ≈ 2^(312-253) = 2^59 samples before bias is detectable
  → 5 bits below the library's 64-bit statistical security target

agree_random generates 634 bits (80 bytes); only 78 bytes are consumed;
the final 2 bytes are silently dropped, confirming the floor truncation.
``` [1](#0-0) [2](#0-1)

### Citations

**File:** src/cbmpc/protocol/hd_keyset_eddsa_2p.cpp (L66-71)
```cpp
  int rand_bitlen = q.get_bits_count() + SEC_P_STAT;
  int rand_size = bits_to_bytes_floor(rand_bitlen);
  if (rv = agree_random(job, 2 * rand_bitlen, rand)) return rv;

  bn_t r_x = bn_t::from_bin(rand.take(rand_size)) % q;
  bn_t r_k = bn_t::from_bin(rand.skip(rand_size).take(rand_size)) % q;
```

**File:** include-internal/cbmpc/internal/core/utils.h (L23-30)
```text
inline int bits_to_bytes_floor(int bits) {
  cb_assert(bits >= 0);
  return bits >> 3;
}
inline int bits_to_bytes(int bits) {
  cb_assert(bits >= 0);
  return (bits + 7) >> 3;
}
```
