### Title
Single Byzantine Participant Can Deliver Forged CKD Output via Unvalidated `AppPublicKey` Variant - (File: crates/contract/src/lib.rs)

### Summary
`respond_ckd` skips all cryptographic output validation when the pending request uses the `AppPublicKey` variant of `CKDAppPublicKey`. A single Byzantine attested participant can call `respond_ckd` with an arbitrary forged `CKDResponse` for any queued `AppPublicKey` CKD request, and the contract will accept it, drain the yield queue, and deliver the forged confidential key to the user — bypassing the threshold-MPC requirement entirely.

### Finding Description
In `respond_ckd` (`crates/contract/src/lib.rs`, lines 675–682), the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no validation at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For the `AppPublicKeyPV` variant, `ckd_output_check` verifies that the supplied `CKDResponse` is cryptographically consistent with the BLS12-381 master public key and the user's ephemeral key pair. For the `AppPublicKey` (legacy, privately-verifiable) variant, the arm is an empty block — the response is accepted unconditionally.

After this match, `resolve_yields_for` is called unconditionally:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
```

`resolve_yields_for` removes the entire queue entry and resumes every parked yield with the supplied bytes. There is no second chance for validation; whatever bytes the attacker supplied are what the user's promise resolves with.

The analog to the external report is direct: just as `_calculateRewards()` always includes the current `epochId` without checking whether it was already claimed (missing state guard), `respond_ckd` always accepts the response for `AppPublicKey` requests without checking whether it is cryptographically valid (missing output guard). In both cases, a single actor can exploit the absent check to produce an unauthorized outcome — repeated reward extraction there, forged key delivery here.

### Impact Explanation
A single Byzantine attested participant (one node, no collusion required) can:
1. Observe any pending `request_app_private_key` call that uses the `AppPublicKey` variant.
2. Immediately call `respond_ckd` with an arbitrary `CKDResponse` (e.g., all-zero `big_y` / `big_c` fields).
3. The contract validates only that the caller is an attested participant and the protocol is active — both trivially satisfied — then drains the yield queue with the forged bytes.
4. The user's promise resolves with the attacker-chosen confidential key material.

The user's application will derive secrets from this forged key. If the application uses the derived key to encrypt data or authenticate, the attacker who knows the forged key can decrypt or impersonate. This constitutes **unauthorized confidential key derivation output without the required participant authorization** and a **bypass of the threshold-signature requirement** (the whole point of MPC is that no single node can unilaterally produce the output).

### Likelihood Explanation
Any single attested participant — including one that is Byzantine but below the signing threshold — can trigger this. The attacker needs only:
- A valid TEE attestation (required to call `respond_ckd`).
- Knowledge of a pending `AppPublicKey` CKD request (observable on-chain).

No collusion, no key leakage, no network-level attack is required. The `AppPublicKey` variant is described as "legacy" but remains fully supported and callable by users today.

### Recommendation
Apply `ckd_output_check` (or an equivalent on-chain verifiable proof) to the `AppPublicKey` variant as well, or remove the `AppPublicKey` variant from the production API and require all new requests to use `AppPublicKeyPV`. If the `AppPublicKey` variant must remain for backward compatibility, document clearly that its security model relies entirely on off-chain verification by the recipient, and consider adding a contract-level warning or deprecation flag.

### Proof of Concept

```
// Precondition: Mallory is an attested participant (one node, below threshold).
// Alice submits a CKD request using the AppPublicKey (legacy) variant.
alice.call(contract, "request_app_private_key", {
    derivation_path: "m/0",
    app_public_key: { AppPublicKey: "<alice_bls_g1_point>" },
    domain_id: 0,
}).deposit(1 yoctoNEAR);

// Mallory observes the pending request on-chain and immediately responds
// with a forged CKDResponse (all-zero field values).
mallory.call(contract, "respond_ckd", {
    request: {
        app_public_key: { AppPublicKey: "<alice_bls_g1_point>" },
        domain_id: 0,
        caller: "alice.near",
        derivation_path: "m/0",
        ...
    },
    response: {
        big_y: "bls12381g1:<all_zeros>",
        big_c: "bls12381g1:<all_zeros>",
    },
});

// Result: the contract's respond_ckd hits the empty AppPublicKey arm,
// skips ckd_output_check entirely, calls resolve_yields_for, and
// Alice's promise resolves with Mallory's forged CKDResponse.
// Alice's application now derives secrets from attacker-controlled key material.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
}
```
