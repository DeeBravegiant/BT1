### Title
Pending Sign/CKD/ForeignTx Requests Permanently Lost When `verify_tee` Sets `accept_requests = false` - (File: `crates/contract/src/lib.rs`)

### Summary

When `verify_tee()` determines that kicking out expired-attestation participants would break the threshold relation, it sets `accept_requests = false` and leaves the contract in `Running` state. All three `respond*` methods (`respond`, `respond_ckd`, `respond_verify_foreign_tx`) gate on this same flag and immediately return `TeeError::TeeValidationFailed`. Any yield-resume promises already parked in `pending_signature_requests`, `pending_ckd_requests`, or `pending_verify_foreign_tx_requests` can never be fulfilled: nodes cannot call `respond*` to deliver their computed signatures, so every in-flight request times out after ~200 blocks and fails permanently.

### Finding Description

`verify_tee()` contains a branch that sets `self.accept_requests = false` when the surviving participant set (after removing expired-attestation nodes) would violate the threshold relation: [1](#0-0) 

The contract stays in `ProtocolContractState::Running` with `accept_requests = false`. All three respond methods then check this flag before resolving any queued yield: [2](#0-1) [3](#0-2) [4](#0-3) 

The yield-resume timeout is `REQUEST_EXPIRATION_BLOCKS = 200` blocks: [5](#0-4) 

Pending requests are stored as `Vec<YieldIndex>` in `LookupMap`s: [6](#0-5) 

Once `accept_requests = false` is set, nodes cannot call `respond*` to resume those yields. After ~200 blocks the NEAR runtime fires the yield-callback with `Err(PromiseError::Failed)`, which pops the yield from the queue and schedules `fail_on_timeout`: [7](#0-6) 

There is no mechanism to deliver responses to already-queued requests while `accept_requests = false`, and no sweep or administrative path to resolve them.

### Impact Explanation

Every sign, CKD, and foreign-transaction-verification request that was in-flight at the moment `verify_tee()` sets `accept_requests = false` is permanently lost. Users whose cross-chain transactions depended on those signatures receive a timeout failure. The pending-request maps retain stale `YieldIndex` entries until each individual yield times out, breaking the request-lifecycle accounting invariant. This matches the Medium allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

### Likelihood Explanation

TEE attestations have a finite validity window (`tee_upgrade_deadline_duration_seconds`). In a production network where participants fail to renew attestations before expiry, `verify_tee()` will naturally reach the `Partial` branch. If the surviving set is too small to satisfy the threshold relation, the flag is set. Any single participant (voter) can trigger `verify_tee()`: [8](#0-7) 

No threshold collusion, no privileged operator access, and no network-level DoS is required. The condition is a normal operational event (attestation expiry under a constrained participant set).

### Recommendation

Separate the "accept new requests" gate from the "deliver responses to existing requests" gate. The `accept_requests` flag should only block `sign`, `request_app_private_key`, and `verify_foreign_transaction` (new submissions). The `respond`, `respond_ckd`, and `respond_verify_foreign_tx` methods should be allowed to resolve already-queued yield-resume promises regardless of `accept_requests`, since those promises were accepted under a valid state and the MPC nodes have already performed the cryptographic work. Concretely, remove the `if !self.accept_requests` guard from all three `respond*` methods, or introduce a separate `accept_responses` flag that is only set to `false` when the contract transitions out of `Running` entirely.

### Proof of Concept

1. Contract is `Running`, `accept_requests = true`. Users submit `sign(...)` calls; yield-resume promises are parked in `pending_signature_requests`.
2. One or more participants' TEE attestations expire.
3. Any participant calls `verify_tee()`. The surviving set is below the threshold relation bound.
4. `verify_tee()` executes `self.accept_requests = false; return Ok(false);` — contract stays `Running`.
5. MPC nodes compute signatures and call `respond(request, response)`.
6. `respond` hits `if !self.accept_requests { return Err(TeeError::TeeValidationFailed.into()); }` and returns an error.
7. After `REQUEST_EXPIRATION_BLOCKS` (~200 blocks, ~4 minutes at 1.2 s/block), the NEAR runtime fires `return_signature_and_clean_state_on_success` with `Err(PromiseError::Failed)`.
8. `pop_oldest_pending_yield` removes the yield index; `fail_on_timeout` is scheduled. The user's transaction fails permanently. [9](#0-8) [2](#0-1) [10](#0-9)

### Citations

**File:** crates/contract/src/lib.rs (L153-155)
```rust
    pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
    pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
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

**File:** crates/contract/src/lib.rs (L1693-1698)
```rust
    pub fn verify_tee(&mut self) -> Result<bool, Error> {
        log!("verify_tee: signer={}", env::signer_account_id());
        // Caller must be a participant (node or operator).
        self.voter_or_panic();
        let ProtocolContractState::Running(running_state) = &mut self.protocol_state else {
            return Err(InvalidState::ProtocolStateNotRunning.into());
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

**File:** crates/contract/src/lib.rs (L2254-2270)
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
```

**File:** crates/node/src/requests/queue.rs (L31-33)
```rust
/// The number of blocks after which a request is assumed to have timed out.
/// This is equal to the yield-resume timeout on the blockchain.
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```

**File:** crates/contract/src/pending_requests.rs (L97-111)
```rust
pub(crate) fn pop_oldest_pending_yield<K>(requests: &mut LookupMap<K, Vec<YieldIndex>>, request: &K)
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let Some(queue) = requests.get_mut(request) else {
        return;
    };
    if queue.is_empty() {
        requests.remove(request);
        return;
    }
    queue.remove(0);
    if queue.is_empty() {
        requests.remove(request);
    }
```
