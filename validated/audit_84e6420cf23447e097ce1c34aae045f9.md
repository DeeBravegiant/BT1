### Title
Single Attested Participant Can Forge CKD Response for `AppPublicKey` Variant — (`File: crates/contract/src/lib.rs`)

### Summary

The `respond_ckd` function in the MPC contract resolves a confidential key derivation (CKD) yield with whatever `(big_y, big_c)` values a single attested participant supplies, without any on-chain cryptographic verification, when the user submitted the request using the `AppPublicKey` (single G1 point) variant. A Byzantine participant strictly below the signing threshold can call `respond_ckd` with a fabricated response — delivering a key pair they control to the user — while the contract accepts it unconditionally.

### Finding Description

The `respond_ckd` function performs a conditional output check:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For the `AppPublicKeyPV` variant, `ckd_output_check` cryptographically verifies that the returned `(big_y, big_c)` pair is consistent with the domain's BLS public key and the user's app public key pair. For the `AppPublicKey` variant — described in the contract README as the "plain G1 point" / legacy path — **no such check is performed**. The contract proceeds directly to resolving the yield with the caller-supplied response:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

The only gate before this is `assert_caller_is_attested_participant_and_protocol_active()`, which requires the caller to be a current attested participant — a single participant, not a threshold quorum. [3](#0-2) 

Contrast this with `respond` for ECDSA/EdDSA signatures, where the contract independently verifies the cryptographic validity of the signature against the derived public key before resolving any yield: [4](#0-3) 

No equivalent on-chain verification exists for `respond_ckd` with `AppPublicKey`.

The `AppPublicKey` variant is the documented legacy/common path per the contract README: [5](#0-4) 

### Impact Explanation

A single Byzantine attested participant acting as the MPC leader for a CKD request can call `respond_ckd` with an arbitrary `CKDResponse { big_y, big_c }`. Because `big_c` is the BLS-encrypted form of the derived app private key (encrypted under the user's `app_public_key`), the attacker can substitute a `big_c` that encrypts a key they already know. The user decrypts `big_c` and receives what they believe is their MPC-derived confidential key — but it is actually a key the attacker controls. This constitutes **unauthorized confidential key derivation output without the required participant authorization**, matching the Critical impact tier: the attacker gains knowledge of the user's supposed secret key material.

### Likelihood Explanation

The `AppPublicKey` variant is the legacy/default path. Any single attested participant who is elected leader for a CKD round can exploit this without threshold cooperation. The TEE attestation requirement is the primary defense, but the contract-level invariant is broken: unlike signature responses (which are cryptographically verified on-chain), CKD responses for `AppPublicKey` are accepted on the word of one participant. A newly added participant — the direct analog to the MetaSwap "newly added adapter" — immediately has this capability upon attestation.

### Recommendation

Apply an on-chain cryptographic output check for the `AppPublicKey` variant analogous to the existing `ckd_output_check` used for `AppPublicKeyPV`. If public verifiability is not possible for the single-G1-point variant by design, the `AppPublicKey` path should be deprecated and removed in favor of `AppPublicKeyPV`, which enforces the check. At minimum, the contract should require threshold-quorum agreement on the CKD response (e.g., collecting `t` matching responses before resolving the yield) rather than accepting the first single-participant submission.

### Proof of Concept

1. User calls `request_app_private_key` with `app_public_key: AppPublicKey(pk_user)` and `domain_id` pointing to a BLS12-381 CKD domain. The contract queues the request.
2. Attacker is an attested participant and is elected leader for this CKD round.
3. Attacker generates a fresh BLS key pair `(sk_evil, pk_evil)` and computes `big_c_evil = Encrypt(sk_evil, pk_user)` and `big_y_evil = pk_evil`.
4. Attacker calls `respond_ckd(request, CKDResponse { big_y: big_y_evil, big_c: big_c_evil })`.
5. The contract executes the `AppPublicKey` branch — no check — and calls `resolve_yields_for`, delivering `big_c_evil` to the user's pending yield.
6. User decrypts `big_c_evil` with their ephemeral private key and obtains `sk_evil`, believing it is their MPC-derived app private key.
7. Attacker already knows `sk_evil` and can sign any transaction on behalf of the user's derived identity. [3](#0-2) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L596-609)
```rust
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
```

**File:** crates/contract/src/lib.rs (L653-689)
```rust
    #[handle_result]
    pub fn respond_ckd(&mut self, request: CKDRequest, response: CKDResponse) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();
        log!("respond_ckd: signer={}, request={:?}", &signer, &request);

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        self.assert_caller_is_attested_participant_and_protocol_active();

        let PublicKeyExtended::Bls12381 {
            public_key: dtos::PublicKey::Bls12381(public_key),
        } = self.public_key_extended(request.domain_id)?
        else {
            env::panic_str("Domain is not compatible with CKD (expected Bls12381 curve)");
        };

        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/README.md (L276-282)
```markdown
#### CKDRequestArgs (Latest version)

The `request_app_private_key` request takes the following arguments:

- `derivation_path` (String): the derivation path.
- `app_public_key`: the ephemeral public key to encrypt the generated confidential key. Accepts either a plain G1 point string (privately verifiable, legacy) or a tagged enum with `AppPublicKey` (single G1 point) or `AppPublicKeyPV` (a `{pk1, pk2}` pair for public verifiability).
- `domain_id` (integer): the domain ID that identifies the key and signature scheme to use to generate the confidential key
```
