### Title
Missing On-Chain Verification for `AppPublicKey` CKD Responses Allows Single Malicious Participant to Deliver Forged Confidential Key - (File: crates/contract/src/lib.rs)

### Summary
The `respond_ckd` function in `MpcContract` performs cryptographic on-chain verification of the CKD response only for the `AppPublicKeyPV` variant, while silently accepting any arbitrary response for the `AppPublicKey` variant. A single malicious attested participant (below threshold) can call `respond_ckd` with a forged `CKDResponse` for any pending `AppPublicKey` request, bypassing the threshold requirement and delivering a key that was never computed by the MPC network.

### Finding Description
In `respond_ckd` at `crates/contract/src/lib.rs:675-682`, the contract branches on `request.app_public_key`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For `AppPublicKeyPV`, `ckd_output_check` verifies that `big_y` and `big_c` in the response are correctly derived from the MPC root public key and the user's app public key pair — preventing any single participant from forging the output. For `AppPublicKey`, the arm is an empty block: the contract performs zero verification on the response values before calling `resolve_yields_for` and delivering the response to the waiting user.

The attack path for a single malicious attested participant:
1. Monitor the chain for a pending `request_app_private_key` call that uses the `AppPublicKey` variant.
2. Call `respond_ckd` with the correct `CKDRequest` (to match the pending map key) and an arbitrary forged `CKDResponse { big_y: <attacker-chosen>, big_c: <attacker-chosen> }`.
3. The contract passes the `is_running_or_resharing`, `accept_requests`, and `assert_caller_is_attested_participant_and_protocol_active` checks, then reaches the match arm, does nothing, and calls `resolve_yields_for` — delivering the forged response to the user.

The user decrypts `big_c - sk * big_y` with their private key `sk` and receives a key chosen entirely by the attacker. Because `AppPublicKey` is "privately verifiable" (the user decrypts with their own key), the user has no on-chain proof that the decrypted value is the correct MPC-derived key, and cannot distinguish a legitimate response from a forged one without out-of-band verification.

### Impact Explanation
A single Byzantine attested participant (strictly below the signing threshold) can unilaterally resolve any pending `AppPublicKey` CKD request with an arbitrary response. The attacker knows the forged key they injected, so any data the user subsequently encrypts under that key is readable by the attacker. This directly breaks the threshold guarantee: the MPC network's threshold of participants is supposed to be required to produce a CKD output, but for `AppPublicKey` requests a single participant suffices. This constitutes unauthorized confidential key derivation output without the required participant authorization — matching the Critical impact category.

### Likelihood Explanation
Medium. The attacker must be a single malicious attested participant (valid TEE, enrolled in the network). They need only watch for `AppPublicKey` CKD requests on-chain and race to call `respond_ckd` before the honest MPC response is submitted. No threshold collusion, no cryptographic break, and no privileged operator access is required.

### Recommendation
Apply the same `ckd_output_check` verification to `AppPublicKey` responses, or — if a single-point proof is not available for that variant — reject `AppPublicKey` requests in `respond_ckd` and require callers to migrate to `AppPublicKeyPV`. At minimum, the inconsistency between the two arms must be resolved so that every CKD response delivered on-chain is cryptographically bound to the MPC root public key.

### Proof of Concept
```rust
// Attacker is an attested participant.
// User has a pending AppPublicKey CKD request with this key:
let ckd_request = CKDRequest::new(
    CKDAppPublicKey::AppPublicKey(some_g1_point),
    domain_id,
    &user_account_id,
    &derivation_path,
);

// Attacker forges a response with arbitrary values:
let forged_response = CKDResponse {
    big_y: dtos::Bls12381G1PublicKey([0xAA; 48]),  // attacker-chosen
    big_c: dtos::Bls12381G1PublicKey([0xBB; 48]),  // attacker-chosen
};

// Contract accepts without any verification (line 676: empty arm):
contract.respond_ckd(ckd_request, forged_response).expect("accepted");

// User receives forged key; attacker knows it; threshold was never reached.
```

The existing test at `crates/contract/src/lib.rs:4452-4541` confirms that an attested *non-participant* is correctly rejected, but an attested *participant* submitting any `CKDResponse` for an `AppPublicKey` request is accepted without cryptographic verification of the response content.