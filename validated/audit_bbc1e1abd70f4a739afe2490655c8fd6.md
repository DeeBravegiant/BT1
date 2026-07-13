The core question is whether `coinbase::convert()` silently ignores trailing bytes, unlike `deser()`. The answer is definitively **yes**, and the vulnerability is real.

---

### Title
Ciphertext Malleability via Trailing-Byte Acceptance in `verify()` and `decrypt()` - (`src/cbmpc/api/pve_base_pke.cpp`)

### Summary

Both `verify()` and `decrypt()` parse the outer `pve_ciphertext_blob_v1_t` using `coinbase::convert()`, which does **not** enforce strict deserialization (no trailing-byte check). An attacker can append arbitrary bytes to a valid ciphertext blob; both functions will accept it and produce identical output to the original, breaking ciphertext uniqueness.

### Finding Description

The `convert.h` header defines two distinct deserialization paths:

**`deser()` — strict, rejects trailing bytes:** [1](#0-0) 

**`convert(T& dst, mem_t src)` — lenient, no trailing-byte check:** [2](#0-1) 

The `deser()` function explicitly checks `converter.get_offset() != converter.get_size()` and returns `E_BADARG` on mismatch. The `convert()` overload returns only `converter.get_rv()` — it never checks whether all input bytes were consumed.

Both `verify()` and `decrypt()` use `coinbase::convert()` for the outer blob parse:

`verify()`: [3](#0-2) 

`decrypt()`: [4](#0-3) 

The `pve_ciphertext_blob_v1_t` struct serializes `version` (4 bytes) followed by `ct` (a length-prefixed `buf_t`): [5](#0-4) 

Because `buf_t` is serialized with a length prefix (the existence of the separate `convert_last` method for "consume all remaining bytes" confirms the regular `convert` path uses a length prefix), any bytes appended **after** the `ct` field in the outer blob are simply not consumed. Since `coinbase::convert()` never checks for unconsumed bytes, the parse succeeds.

The codebase's own test suite documents this exact malleability risk for `deser()`: [6](#0-5) 

The comment reads: *"This prevents message malleability where two different byte sequences could deserialize to the same value."* The production `verify()`/`decrypt()` paths do not use `deser()`, so they lack this protection.

### Impact Explanation

Two distinct byte sequences — `ciphertext` and `ciphertext || <garbage>` — pass `verify()` and `decrypt()` identically, producing the same verified public key and the same decrypted plaintext. This breaks the binding property of the ciphertext: the raw byte representation is no longer a canonical identifier for the cryptographic object. Any application-level deduplication, replay detection, audit log, or commitment scheme keyed on raw ciphertext bytes can be bypassed by an attacker who appends trailing garbage to a legitimately obtained ciphertext.

### Likelihood Explanation

The attacker only needs to be an unprivileged API caller who can supply a `ciphertext` argument. No key material, threshold collusion, or privileged access is required. The modification is trivial (append any bytes within `MAX_CIPHERTEXT_BLOB_SIZE`). The fix is a one-line change.

### Recommendation

Replace `coinbase::convert(blob, ciphertext)` with `coinbase::deser(ciphertext, blob)` in `verify()`, `decrypt()`, `get_public_key_compressed()`, and `get_Label()`. Apply the same fix to the analogous `coinbase::convert(pve_ct, blob.ct)` inner parse if `ec_pve_t` serialization is also length-prefixed. Audit all other public API entry points in `pve_batch_single_recipient.cpp` and `pve_batch_ac.cpp` for the same pattern.

### Proof of Concept

```cpp
// 1. Encrypt normally to get a valid ciphertext blob
buf_t ct_original;
encrypt(curve_id::secp256k1, ek, label, x, ct_original);  // succeeds

// 2. Append 4 garbage bytes to the outer blob
buf_t ct_malleable = ct_original;
byte_t garbage[4] = {0xDE, 0xAD, 0xBE, 0xEF};
ct_malleable.append(mem_t(garbage, 4));

// 3. Both verify() calls succeed and agree
assert(verify(curve_id::secp256k1, ek, ct_original,  Q_compressed, label) == SUCCESS);
assert(verify(curve_id::secp256k1, ek, ct_malleable, Q_compressed, label) == SUCCESS);

// 4. Both decrypt() calls succeed and produce identical plaintext
buf_t x_orig, x_mall;
assert(decrypt(curve_id::secp256k1, dk, ek, ct_original,  label, x_orig) == SUCCESS);
assert(decrypt(curve_id::secp256k1, dk, ek, ct_malleable, label, x_mall) == SUCCESS);
assert(x_orig == x_mall);

// 5. But the raw byte sequences differ — breaking any dedup/replay check
assert(ct_original != ct_malleable);
```

### Citations

**File:** include-internal/cbmpc/internal/core/convert.h (L254-266)
```text
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

**File:** include-internal/cbmpc/internal/core/convert.h (L277-282)
```text
error_t convert(T& dst, mem_t src) {
  if (src.size < 0 || (src.size && !src.data)) return coinbase::error(E_BADARG);
  converter_t converter(src);
  converter.convert(dst);
  return converter.get_rv();
}
```

**File:** src/cbmpc/api/pve_base_pke.cpp (L19-24)
```cpp
struct pve_ciphertext_blob_v1_t {
  uint32_t version = pve_ciphertext_version_v1;
  buf_t ct;  // serialized `coinbase::mpc::ec_pve_t`

  void convert(coinbase::converter_t& c) { c.convert(version, ct); }
};
```

**File:** src/cbmpc/api/pve_base_pke.cpp (L225-231)
```cpp
  pve_ciphertext_blob_v1_t blob;
  if (rv = coinbase::convert(blob, ciphertext)) return rv;
  if (blob.version != pve_ciphertext_version_v1) return coinbase::error(E_FORMAT, "unsupported ciphertext version");

  base_pke_bridge_t bridge(base_pke);
  coinbase::mpc::ec_pve_t pve_ct;
  if (rv = coinbase::convert(pve_ct, blob.ct)) return rv;
```

**File:** src/cbmpc/api/pve_base_pke.cpp (L264-270)
```cpp
  pve_ciphertext_blob_v1_t blob;
  if (rv = coinbase::convert(blob, ciphertext)) return rv;
  if (blob.version != pve_ciphertext_version_v1) return coinbase::error(E_FORMAT, "unsupported ciphertext version");

  base_pke_bridge_t bridge(base_pke);
  coinbase::mpc::ec_pve_t pve_ct;
  if (rv = coinbase::convert(pve_ct, blob.ct)) return rv;
```

**File:** tests/unit/core/test_convert.cpp (L467-484)
```cpp
TEST(CoreConvert, RejectsTrailingBytes) {
  // Strict deserialization should fail when there are unconsumed trailing bytes.
  // This prevents message malleability where two different byte sequences
  // could deserialize to the same value.
  int original_value = 42;
  buf_t serialized = coinbase::ser(original_value);

  byte_t garbage[4] = {0xDE, 0xAD, 0xBE, 0xEF};
  buf_t with_trailing = serialized + mem_t(garbage, 4);

  int result = 0;
  error_t rv = deser(with_trailing, result);

  EXPECT_NE(rv, SUCCESS);

  EXPECT_OK(deser(serialized, result));
  EXPECT_EQ(result, original_value);
}
```
