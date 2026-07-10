Let me verify the exact `to_ecrecover_bytes` location and the `respond` flow more carefully.

### Title
Byzantine Participant Can Submit `K256Signature` with Invalid `big_r` Prefix Byte That Passes Contract Verification but Fails Downstream Decompression — (`crates/near-mpc-crypto-types/src/conversions/near.rs`, `crates/near-mpc-signature-verifier/src/lib.rs`, `crates/contract/src/lib.rs`)

---

### Summary

`contract::respond` verifies a `K256Signature` exclusively via `ecrecover`, which only consumes the x-coordinate of R (`big_r.affine_point[1..33]`) and ignores the prefix byte (`affine_point[0]`). A Byzantine attested participant can therefore submit a signature whose `big_r.affine_point[0]` is `0x04` (or any byte other than `0x02`/`0x03`). The contract accepts and stores the signature. Any downstream consumer that calls `TryFrom<&K256AffinePoint> for k256::AffinePoint` on the returned value receives `Err(InvalidPoint)`, because `k256::AffinePoint::from_bytes` requires a valid compressed-point prefix.

---

### Finding Description

**Root cause — `to_ecrecover_bytes` discards the prefix byte:** [1](#0-0) 

`bytes[..32]` is filled from `affine_point[1..]` only; `affine_point[0]` is never read.

**Verification path — `verify_ecdsa_signature` relies solely on `ecrecover`:** [2](#0-1) 

`ecrecover` receives the 64-byte `r ‖ s` blob and the `recovery_id`. It never sees `affine_point[0]`. A signature with `affine_point[0] = 0x04` and a valid `(r, s, recovery_id)` tuple passes this check identically to a well-formed one.

**Contract acceptance — no prefix validation before storage:** [3](#0-2) 

After `verify_ecdsa_signature` returns `Ok`, the full `response` (including the malformed `big_r`) is serialized and forwarded to the waiting user promise via `serde_json::to_vec(&response)`. No prefix check is performed anywhere in this path.

**Downstream failure — `TryFrom<&K256AffinePoint>` requires a compressed prefix:** [4](#0-3) 

`k256::AffinePoint::from_bytes` accepts only `0x02` or `0x03` as the leading byte of a 33-byte encoding. A `0x04` prefix causes it to return `CtOption::None`, which is mapped to `Err(InvalidPoint)`.

**Honest-node path always produces a valid prefix (not enforced by contract):** [5](#0-4) 

`K256Signature::from_ecdsa_recoverable` always writes `0x02` or `0x03` based on `recovery_id.is_y_odd()`. The contract never enforces this invariant; it is only upheld by honest nodes.

---

### Impact Explanation

A Byzantine attested participant can cause the chain-signature contract to emit a `K256Signature` whose `big_r` cannot be decompressed into a `k256::AffinePoint`. Any consumer that calls `TryFrom<&K256AffinePoint> for k256::AffinePoint` on the returned signature — for example, to reconstruct the full R point for a custom verification protocol or to re-derive the y-coordinate — receives an unrecoverable `Err(InvalidPoint)`. The contract-accepted signature is permanently malformed in its serialized form; there is no way for the caller to repair it without knowing the correct parity bit. This breaks the invariant that every `K256Signature` accepted by `contract::respond` is decompressible via the standard library conversion path.

Standard Ethereum-style `ecrecover`-based verification (which only needs the x-coordinate and `recovery_id`) is unaffected, so the signature remains usable for that specific purpose. The impact is scoped to consumers that rely on the full affine R point.

---

### Likelihood Explanation

Requires a single Byzantine attested participant — the node that calls `respond`. No threshold collusion is needed; `respond` is callable by any one attested participant. The attack requires the Byzantine node to have participated in a threshold signing round (to obtain a valid `(r, s)` pair) and then to substitute `0x04` for the prefix byte before submitting. This is a trivial one-byte modification at the call site.

---

### Recommendation

Add a prefix-byte validation step inside `contract::respond` (or inside `verify_ecdsa_signature`) before accepting a `K256Signature`:

```rust
let prefix = signature_response.big_r.affine_point[0];
if prefix != 0x02 && prefix != 0x03 {
    return Err(RespondError::InvalidSignature.into());
}
```

Alternatively, validate inside `to_ecrecover_bytes` or add a dedicated `K256AffinePoint::validate_compressed_prefix` method called from the verifier, so the invariant is enforced at the library boundary rather than only at the contract level.

---

### Proof of Concept

```rust
#[test]
fn byzantine_big_r_prefix_passes_verify_but_fails_decompression() {
    use k256::ecdsa::{SigningKey, signature::hazmat::PrehashSigner};
    use near_mpc_crypto_types::primitives::{K256AffinePoint, K256Scalar, K256Signature};
    use near_mpc_signature_verifier::verify_ecdsa_signature;
    use near_mpc_crypto_types::crypto::Secp256k1PublicKey;

    let mut rng = rand::rngs::StdRng::from_seed([7u8; 32]);
    let signing_key = SigningKey::random(&mut rng);
    let msg = [42u8; 32];
    let (sig, recovery_id) = signing_key.sign_prehash_recoverable(&msg).unwrap();

    // Honest signature has prefix 0x02 or 0x03; Byzantine node substitutes 0x04.
    let mut affine_point = [0u8; 33];
    affine_point[0] = 0x04;                          // ← invalid compressed prefix
    affine_point[1..].copy_from_slice(&sig.r().to_bytes());

    let bad_sig = K256Signature {
        big_r: K256AffinePoint { affine_point },
        s: K256Scalar { scalar: sig.s().to_bytes().into() },
        recovery_id: recovery_id.to_byte(),
    };

    let pk = k256::PublicKey::from(signing_key.verifying_key());
    let secp_pk = Secp256k1PublicKey::from(&pk);

    // Step 1: contract verification passes (ecrecover ignores prefix byte).
    assert!(verify_ecdsa_signature(&bad_sig, &msg, &secp_pk).is_ok());

    // Step 2: downstream decompression fails.
    let result = k256::AffinePoint::try_from(&bad_sig.big_r);
    assert!(result.is_err()); // Err(InvalidPoint)
}
```

### Citations

**File:** crates/near-mpc-crypto-types/src/conversions/near.rs (L98-103)
```rust
    pub fn to_ecrecover_bytes(&self) -> [u8; 64] {
        let mut bytes = [0u8; 64];
        bytes[..32].copy_from_slice(&self.big_r.affine_point[1..]);
        bytes[32..].copy_from_slice(&self.s.scalar);
        bytes
    }
```

**File:** crates/near-mpc-signature-verifier/src/lib.rs (L17-27)
```rust
    let sig_bytes = signature.to_ecrecover_bytes();

    // ecrecover with malleability_flag=true validates r < n and s < n/2,
    // then recovers the public key from the signature.
    let recovered = near_sdk::env::ecrecover(message, &sig_bytes, signature.recovery_id, true)
        .ok_or(VerificationError::FailedToRecoverSignature)?;

    if recovered != public_key.0 {
        return Err(VerificationError::RecoveredPkDoesNotMatchExpectedKey);
    }
    Ok(())
```

**File:** crates/contract/src/lib.rs (L602-649)
```rust
                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
            }
            (
                dtos::SignatureResponse::Ed25519 { signature },
                PublicKeyExtended::Ed25519 {
                    edwards_point: public_key_edwards_point,
                    ..
                },
            ) => {
                let derived_public_key_edwards_point = derive_public_key_edwards_point_ed25519(
                    &public_key_edwards_point,
                    &request.tweak,
                );
                let derived_public_key_32_bytes =
                    dtos::Ed25519PublicKey::from(derived_public_key_edwards_point.compress());

                let message = request.payload.as_eddsa().expect("Payload is not EdDSA");

                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    message,
                    &derived_public_key_32_bytes,
                )
                .is_ok()
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_signature_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
```

**File:** crates/near-mpc-crypto-types/src/conversions/k256.rs (L24-31)
```rust
impl TryFrom<&K256AffinePoint> for k256::AffinePoint {
    type Error = CryptoConversionError;
    fn try_from(dto: &K256AffinePoint) -> Result<Self, Self::Error> {
        k256::AffinePoint::from_bytes(&dto.affine_point.into())
            .into_option()
            .ok_or(CryptoConversionError::InvalidPoint)
    }
}
```

**File:** crates/near-mpc-crypto-types/src/conversions/k256.rs (L85-101)
```rust
    pub fn from_ecdsa_recoverable(
        sig: &k256::ecdsa::Signature,
        recovery_id: k256::ecdsa::RecoveryId,
    ) -> Self {
        let prefix = if recovery_id.is_y_odd() { 0x03 } else { 0x02 };
        let mut affine_point = [0u8; 33];
        affine_point[0] = prefix;
        affine_point[1..].copy_from_slice(&sig.r().to_bytes());

        K256Signature {
            big_r: K256AffinePoint { affine_point },
            s: K256Scalar {
                scalar: sig.s().to_bytes().into(),
            },
            recovery_id: recovery_id.to_byte(),
        }
    }
```
