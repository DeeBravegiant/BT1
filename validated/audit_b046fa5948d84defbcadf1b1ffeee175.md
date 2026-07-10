### Title
Single Attested Participant Can Deliver Arbitrary CKD Output Without Threshold Authorization — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in the MPC contract skips all cryptographic verification of the CKD response when the request uses the `AppPublicKey` variant. A single Byzantine participant (1-of-n, strictly below the signing threshold) can unilaterally deliver arbitrary key material to users, bypassing the threshold protocol entirely.

---

### Finding Description

In `respond_ckd`, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← NO VERIFICATION
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For the `AppPublicKeyPV` variant, a pairing-based `ckd_output_check` is performed, cryptographically binding the response to the threshold public key and the user's app key pair. For the `AppPublicKey` (legacy) variant, the branch is a complete no-op — the contract performs zero verification of `big_y` or `big_c` in the `CKDResponse`.

The only gate before this branch is `assert_caller_is_attested_participant_and_protocol_active`, which is a **1-of-n** check, not a **t-of-n** threshold check: [2](#0-1) 

After the no-op branch, `resolve_yields_for` immediately drains all pending yields for the request, delivering the unverified response to every waiting caller: [3](#0-2) 

This contrasts sharply with `respond` (ECDSA/EdDSA), which verifies the submitted signature against the on-chain stored public key before resolving yields — ensuring only a threshold-protocol-produced signature can be accepted: [4](#0-3) 

The `AppPublicKey` variant is still accepted by the contract. The README describes it as "privately verifiable, legacy" but it is not deprecated or rejected in code: [5](#0-4) 

---

### Impact Explanation

A single Byzantine participant can:

1. Observe a pending `request_app_private_key` call using the `AppPublicKey` variant.
2. Call `respond_ckd(request, CKDResponse { big_y: attacker_chosen, big_c: attacker_chosen })`.
3. The contract accepts it without any cryptographic check and delivers it to the user via `resolve_yields_for`.

The user receives attacker-controlled key material. Since the attacker chose the values, they know the "derived" key, enabling decryption of any data the user encrypts to it, or impersonation in any system relying on that key. This constitutes **confidential key derivation output without the required participant authorization** — the threshold protocol is bypassed entirely for this request type.

---

### Likelihood Explanation

Medium. The attacker must be an attested participant (requires TEE attestation and inclusion in the participant set). However, only **1-of-n** participants needs to be Byzantine — well below the signing threshold t (where t > n/2). The `AppPublicKey` variant remains accepted by the contract and is described as the "legacy" path, meaning existing integrations may still use it.

---

### Recommendation

For `AppPublicKey` requests, implement an equivalent cryptographic binding between the response and the threshold public key, or formally deprecate and reject the `AppPublicKey` variant at the contract level, requiring all CKD callers to use `AppPublicKeyPV` so that on-chain pairing verification is always enforced.

---

### Proof of Concept

```
1. User calls request_app_private_key({
       app_public_key: AppPublicKey(any_g1_point),
       derivation_path: "...",
       domain_id: ...,
   })
   → pending CKD request queued in contract

2. Byzantine participant (1 of n) calls respond_ckd(request, CKDResponse {
       big_y: attacker_controlled_bytes,
       big_c: attacker_controlled_bytes,
   })

3. Contract executes lib.rs:675-682:
       AppPublicKey(_) => {}   ← no-op, zero verification

4. resolve_yields_for drains all pending yields, delivering the
   attacker-chosen response to the user.

5. User receives key material the attacker fully controls.
```

**Analog mapping:** Just as `ERC20Votes(token).delegate(msg.sender)` unconditionally delegates ALL staked voting power to the last staker regardless of their stake amount, `respond_ckd` unconditionally accepts ANY CKD output from a single participant for `AppPublicKey` requests, regardless of whether the threshold protocol was executed — delivering disproportionate (total) control over the derived key to a single Byzantine actor.

### Citations

**File:** crates/contract/src/lib.rs (L586-644)
```rust
        let signature_is_valid = match (&response, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                // generate the expected public key
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");
                let affine = *k256::PublicKey::try_from(&secp_pk)
                    .expect("stored key is always valid")
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

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
```

**File:** crates/contract/src/lib.rs (L666-666)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();
```

**File:** crates/contract/src/lib.rs (L675-682)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }
```

**File:** crates/contract/src/lib.rs (L684-688)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/contract/README.md (L281-281)
```markdown
- `app_public_key`: the ephemeral public key to encrypt the generated confidential key. Accepts either a plain G1 point string (privately verifiable, legacy) or a tagged enum with `AppPublicKey` (single G1 point) or `AppPublicKeyPV` (a `{pk1, pk2}` pair for public verifiability).
```
