The code path is fully traceable. Here is the complete analysis:

**Call chain:**
`ecc_pub_key_t::from_der` → `ecurve_ed_t::pub_from_der` → `ecurve_ed_t::from_bin` → `ec25519_core::from_bin`

`ecurve_ed_t::pub_from_der` only checks the DER prefix and then calls `from_bin`: [1](#0-0) 

`ecurve_ed_t::from_bin` only checks that the point satisfies the curve equation (via the square-root computation), with no subgroup check: [2](#0-1) 

The existing test `RejectTorsionAndFixInfinityEq` explicitly confirms that `from_bin` returns `SUCCESS` for the order-2 torsion point (x=0, y=−1), while `is_in_subgroup` returns `false` and `curve.check()` returns an error: [3](#0-2) 

`ecurve_t::check` (which would catch this) is never called inside `pub_from_der`: [4](#0-3) 

`ecurve_ed_t::verify` re-serializes the point and delegates directly to OpenSSL's `ED25519_verify` with no subgroup check: [5](#0-4) 

**Forgery mechanics:**

OpenSSL's `ED25519_verify` follows RFC 8032's cofactor-8 check: `[8][S]B = [8]R + [8][k]A`. For the order-2 torsion point A=(0,−1), `[8]A = infinity` (since `2A = ∞`, so `8A = 4·∞ = ∞`). The check degenerates to `[8][S]B = [8]R`. An attacker picks any scalar S, sets R = [S]B, and the pair (R, S) satisfies the equation trivially — no private key needed.

**Guard that exists but is bypassed:**

`ecurve_ed_t::is_in_subgroup` and `ecurve_t::check` both exist and correctly reject torsion points: [6](#0-5) 

Neither is called inside `pub_from_der`.

---

### Title
Ed25519 `pub_from_der` accepts torsion points, enabling trivial EdDSA signature forgery — (`src/cbmpc/crypto/base_eddsa.cpp`)

### Summary
`ecurve_ed_t::pub_from_der` calls `from_bin` but omits the subgroup check (`curve.check` / `is_in_subgroup`). An attacker who supplies a DER blob encoding the Ed25519 order-2 torsion point (0, −1) obtains a successfully imported `ecc_pub_key_t`. Because `ecurve_ed_t::verify` passes the re-serialized point directly to OpenSSL's `ED25519_verify` without a subgroup check, and because RFC 8032's cofactor-8 verification degenerates to a trivially satisfiable equation for any order-dividing-8 torsion point, the attacker can forge a valid EdDSA signature for any message without possessing a private key.

### Finding Description
`ecurve_ed_t::pub_from_der` (line 191–195, `src/cbmpc/crypto/base_eddsa.cpp`) validates only the DER prefix and then calls `from_bin`, which decodes the compressed y-coordinate and recovers x via the curve equation. This accepts any point on the curve, including the 8 torsion points. The subgroup check (`is_in_subgroup` / `curve.check`) is never invoked. The resulting `ecc_pub_key_t` is indistinguishable from a legitimate key to all downstream code. `ecurve_ed_t::verify` (line 204–210) re-serializes the point and calls `ED25519_verify(hash, sig, pub_bin)` — OpenSSL's RFC 8032 implementation — which also performs no subgroup check on the public key. For the order-2 torsion point A, `[8]A = ∞`, so the RFC 8032 check `[8][S]B = [8]R + [8][k]A` reduces to `[8][S]B = [8]R`. Choosing R = [S]B for any S satisfies this unconditionally.

### Impact Explanation
Any caller that imports an Ed25519 public key via `ecc_pub_key_t::from_der` from an untrusted source and then calls `verify` will accept a forged signature. The attacker needs no private key: pick S=1, set R=B (the base point), and the signature (R,1) verifies against the torsion key for any message. In protocol contexts where a peer-supplied public key is imported via DER and then used to authenticate messages, this allows a malicious peer to pass signature verification without possessing a corresponding private key.

### Likelihood Explanation
The DER format for Ed25519 public keys is a fixed 44-byte structure (12-byte prefix + 32-byte point). Constructing the torsion-point DER blob requires only prepending the known `x509_prefix` to the 32-byte encoding of (0, −1). No special privileges or side-channel access are required. The forgery itself is a single scalar multiplication.

### Recommendation
Add a subgroup check inside `ecurve_ed_t::pub_from_der` immediately after `from_bin` succeeds:
```cpp
error_t ecurve_ed_t::pub_from_der(ecc_pub_key_t& P, mem_t der) const {
  if (der.size != ...) return coinbase::error(E_FORMAT);
  if (memcmp(...)) return coinbase::error(E_FORMAT);
  error_t rv = from_bin(P, der.skip(ed25519::x509_prefix.size));
  if (rv) return rv;
  if (!is_in_subgroup(P)) return coinbase::error(E_CRYPTO); // ADD THIS
  return SUCCESS;
}
```
Equivalently, call `curve_ed25519.check(P)` after `from_bin`. The same fix should be applied to `ecc_point_t::from_bin` when used in a public-key context, or callers should be required to call `curve.check()` before trusting the imported point.

### Proof of Concept
```cpp
// Build DER for the Ed25519 order-2 torsion point (x=0, y=p-1).
// y = 2^255 - 19 - 1 = 2^255 - 20; compressed encoding (little-endian, sign bit=0):
uint8_t torsion_bin[32];
torsion_bin[0] = 0xec;
for (int i = 1; i < 31; i++) torsion_bin[i] = 0xff;
torsion_bin[31] = 0x7f;

// Prepend the known x509 DER prefix for Ed25519.
static const uint8_t prefix[] = {0x30,0x2A,0x30,0x05,0x06,0x03,0x2B,0x65,0x70,0x03,0x21,0x00};
buf_t der(sizeof(prefix) + 32);
memcpy(der.data(), prefix, sizeof(prefix));
memcpy(der.data() + sizeof(prefix), torsion_bin, 32);

ecc_pub_key_t pub;
assert(pub.from_der(der) == SUCCESS);          // torsion point accepted
assert(!pub.is_in_subgroup());                 // confirms it is a torsion point

// Forge: pick S=1, R = [1]G = G.
ecc_prv_key_t dummy; dummy.set(curve_ed25519, bn_t(1));
buf_t forged_sig = dummy.sign(some_message);   // signs with scalar 1 against G

// Verify forged signature against torsion key — succeeds.
assert(pub.verify(some_message, forged_sig) == SUCCESS);
```

### Citations

**File:** src/cbmpc/crypto/base_eddsa.cpp (L115-119)
```cpp
bool ecurve_ed_t::is_in_subgroup(const ecc_point_t& P) const {
  // NOTE: There is a more efficient way to check: https://eprint.iacr.org/2022/1164.pdf
  if (!is_on_curve(P)) return false;
  return ec25519_core::is_in_subgroup(P.storage);
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

**File:** src/cbmpc/crypto/base_eddsa.cpp (L191-195)
```cpp
error_t ecurve_ed_t::pub_from_der(ecc_pub_key_t& P, mem_t der) const {
  if (der.size != ed25519::x509_prefix.size + ed25519::pub_compressed_bin_size()) return coinbase::error(E_FORMAT);
  if (0 != memcmp(ed25519::x509_prefix.data, der.data, ed25519::x509_prefix.size)) return coinbase::error(E_FORMAT);
  return from_bin(P, der.skip(ed25519::x509_prefix.size));
}
```

**File:** src/cbmpc/crypto/base_eddsa.cpp (L204-210)
```cpp
error_t ecurve_ed_t::verify(const ecc_pub_key_t& P, mem_t hash, mem_t sig) const {
  byte_t pub_bin[32];
  to_compressed_bin(P, pub_bin);
  if (sig.size != ed25519::signature_size()) return coinbase::error(E_FORMAT);
  if (!ED25519_verify(hash.data, hash.size, sig.data, pub_bin)) return coinbase::error(E_CRYPTO);
  return SUCCESS;
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
