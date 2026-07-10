### Title
`accept_requests = false` Blocks `respond()` for Already-Pending Requests, Permanently Preventing Signature Delivery - (`crates/contract/src/lib.rs`)

---

### Summary

When `verify_tee()` sets `accept_requests = false` due to insufficient valid TEE attestations, it inadvertently blocks `respond()`, `respond_ckd()`, and `respond_verify_foreign_tx()` for requests that were already accepted and are sitting in the pending-request queues. This is a direct analog to H-5: a state-flag transition that should only gate *new* intake also gates the *response delivery* path for already-queued work, breaking the request lifecycle invariant.

---

### Finding Description

`MpcContract` stores an `accept_requests: bool` field: [1](#0-0) 

`check_request_preconditions` (called by `sign`, `request_app_private_key`, `verify_foreign_transaction`) correctly refuses new intake when the flag is `false`: [2](#0-1) 

However, the three node-facing response functions apply the **same guard**:

`respond()`: [3](#0-2) 

`respond_ckd()`: [4](#0-3) 

`respond_verify_foreign_tx()`: [5](#0-4) 

`verify_tee()` sets `accept_requests = false` when kicking out participants with expired attestations would leave fewer than the threshold: [6](#0-5) 

Once `accept_requests = false`, every call to `respond*` returns `TeeError::TeeValidationFailed`. The pending-request maps (`pending_signature_requests`, `pending_ckd_requests`, `pending_verify_foreign_tx_requests`) still hold the queued `YieldIndex` entries: [7](#0-6) 

`resolve_yields_for` — the only path that resumes those yields — is never reached: [8](#0-7) 

The yields sit unresolved until the NEAR runtime fires the ~200-block yield-timeout, at which point `return_signature_and_clean_state_on_success` (or its CKD/foreign-tx counterpart) is invoked with `Err(PromiseError::Failed)`, pops the oldest entry, and chains `fail_on_timeout` — failing the user's transaction: [9](#0-8) 

The 1 yoctoNEAR deposit is refunded, but the signature is never delivered and the user's cross-chain operation fails.

---

### Impact Explanation

This matches **Medium: request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants.** Requests that were validly accepted and for which the MPC network has already computed a threshold signature cannot be delivered to the caller. Every pending yield in all three request maps is stranded until timeout. The invariant "an accepted request will eventually receive a response" is violated by a state-flag transition that was only intended to gate new intake.

---

### Likelihood Explanation

`verify_tee()` is callable by any current participant: [10](#0-9) 

Attestation expiry is a routine operational event (certificates have finite lifetimes). In a network where the participant count equals the governance threshold (e.g., a 3-of-3 setup), a single expired attestation triggers the `Partial` branch and, because kicking the node would drop below threshold, sets `accept_requests = false`. A single Byzantine participant below the signing threshold can also call `verify_tee()` at a moment when a peer's attestation has just expired to deliberately trigger the flag. No collusion above threshold is required.

---

### Recommendation

Remove the `accept_requests` guard from `respond()`, `respond_ckd()`, and `respond_verify_foreign_tx()`. The flag's purpose is to prevent *new* requests from entering the system when TEE health is uncertain; it should not block delivery of responses to requests that were already accepted. The cryptographic signature verification already present in each `respond*` function is sufficient to reject any invalid output regardless of node TEE status.

---

### Proof of Concept

1. Contract is Running; `accept_requests = true`.
2. User calls `sign(...)` — request is queued in `pending_signature_requests` with a live `YieldIndex`.
3. MPC nodes complete the threshold signing protocol and prepare a valid `SignatureResponse`.
4. Before any node calls `respond()`, a participant calls `verify_tee()`. One peer's attestation has just expired; kicking it would drop the set below threshold, so `accept_requests` is set to `false`.
5. Every node's call to `respond(request, response)` now returns `Err(TeeError::TeeValidationFailed)` at line 579–581 — the `resolve_yields_for` call is never reached.
6. After ~200 blocks the NEAR runtime fires the yield-timeout callback; `return_signature_and_clean_state_on_success` receives `Err(PromiseError::Failed)`, pops the yield, and chains `fail_on_timeout`.
7. The user's transaction fails. The 1 yoctoNEAR deposit is refunded, but the signature is permanently lost and the cross-chain operation must be retried from scratch.

### Citations

**File:** crates/contract/src/lib.rs (L153-155)
```rust
    pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
    pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
```

**File:** crates/contract/src/lib.rs (L162-162)
```rust
    accept_requests: bool,
```

**File:** crates/contract/src/lib.rs (L298-302)
```rust
        // 4. Refuse the request if the contract is not currently accepting requests
        //    (e.g. because TEE validation has failed).
        if !self.accept_requests {
            env::panic_str(&TeeError::TeeValidationFailed.to_string())
        }
```

**File:** crates/contract/src/lib.rs (L579-581)
```rust
        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L662-664)
```rust
        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L711-713)
```rust
        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L1693-1697)
```rust
    pub fn verify_tee(&mut self) -> Result<bool, Error> {
        log!("verify_tee: signer={}", env::signer_account_id());
        // Caller must be a participant (node or operator).
        self.voter_or_panic();
        let ProtocolContractState::Running(running_state) = &mut self.protocol_state else {
```

**File:** crates/contract/src/lib.rs (L1727-1738)
```rust
                if let Err(err) = ThresholdParameters::validate_governance_against_reconstruction(
                    u64::try_from(remaining).expect("participant count fits in u64"),
                    current_params.threshold(),
                    max_reconstruction_threshold,
                ) {
                    log!(
                        "Kicking out participants with an invalid TEE status would break the threshold relation ({:?}); {} participants remain with a valid TEE status. This requires manual intervention. We will not accept new signature requests as a safety precaution.",
                        err,
                        remaining,
                    );
                    self.accept_requests = false;
                    return Ok(false);
```

**File:** crates/contract/src/lib.rs (L2254-2271)
```rust
        match signature {
            Ok(signature) => PromiseOrValue::Value(signature),
            Err(_) => {
                pending_requests::pop_oldest_pending_yield(
                    &mut self.pending_signature_requests,
                    &request,
                );

                let fail_on_timeout_gas = Gas::from_tgas(self.config.fail_on_timeout_tera_gas);
                let promise = Promise::new(env::current_account_id()).function_call(
                    method_names::FAIL_ON_TIMEOUT.to_string(),
                    vec![],
                    NearToken::from_near(0),
                    fail_on_timeout_gas,
                );
                near_sdk::PromiseOrValue::Promise(promise.as_return())
            }
        }
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
