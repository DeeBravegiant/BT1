### Title
`accept_requests = false` in `verify_tee()` Blocks Completion of Already-Queued Signature Requests — (File: `crates/contract/src/lib.rs`)

---

### Summary

When `verify_tee()` sets `accept_requests = false` due to a TEE validation failure, it not only blocks new signature requests but also prevents MPC nodes from calling `respond()`, `respond_ckd()`, or `respond_verify_foreign_tx()` to complete already-queued, in-flight requests. Those pending yield-resume promises will time out, causing users' cross-chain transactions to fail irreversibly.

---

### Finding Description

`verify_tee()` sets `self.accept_requests = false` when the TEE validation result is `Partial` and kicking out the invalid participants would break the threshold relation: [1](#0-0) 

The `accept_requests` flag is then checked in **all three `respond*` methods**, not just in the new-request entry points:

```rust
// respond()
if !self.accept_requests {
    return Err(TeeError::TeeValidationFailed.into());
}
``` [2](#0-1) [3](#0-2) [4](#0-3) 

The same flag is also checked in `check_request_preconditions()`, which gates new `sign()`, `request_app_private_key()`, and `verify_foreign_transaction()` calls: [5](#0-4) 

The comment in `verify_tee()` says *"We will not accept **new** signature requests as a safety precaution"*, but the implementation also blocks the completion of **already-accepted** requests. Requests that were enqueued before the flag was set are stored in `pending_signature_requests`, `pending_ckd_requests`, and `pending_verify_foreign_tx_requests` as yield-resume promises: [6](#0-5) 

When `respond()` is blocked, those yields cannot be resolved. NEAR's yield-resume mechanism has a finite timeout; once it expires, `return_signature_and_clean_state_on_success` fires the error branch, pops the queue entry, and calls `fail_on_timeout`: [7](#0-6) [8](#0-7) 

Every in-flight request is therefore forcibly failed.

---

### Impact Explanation

**Medium.** This breaks the request-lifecycle invariant: a request that was accepted by the contract and is actively being processed by MPC nodes cannot be completed. Users whose `sign()` / `request_app_private_key()` / `verify_foreign_transaction()` calls were in-flight at the moment `accept_requests` is set to `false` will receive a `Timeout` error. For bridge or cross-chain use cases this means the foreign-chain transaction they were trying to authorize fails, potentially causing loss of funds or missed settlement windows on the foreign chain. The impact matches the allowed medium category: *"request-lifecycle … manipulation that breaks production safety/accounting invariants."*

---

### Likelihood Explanation

`verify_tee()` requires only a single participant (`voter_or_panic()`), which is below the signing threshold: [9](#0-8) 

The `Partial` + threshold-break path is realistic during any TEE software upgrade window, when some nodes have not yet refreshed their attestations. A Byzantine participant below the signing threshold can deliberately call `verify_tee()` at a moment when at least one other participant's attestation has expired, reliably triggering `accept_requests = false` and stranding all in-flight requests.

---

### Recommendation

Remove the `accept_requests` guard from `respond()`, `respond_ckd()`, and `respond_verify_foreign_tx()`. The flag should only gate **new** request submission (i.e., `check_request_preconditions()`). Requests that were already accepted and are being processed by the MPC network should always be completable, regardless of the TEE validation state. This mirrors the recommendation in the reference report: allow "exit" operations (completing existing requests) while blocking new potentially harmful actions.

---

### Proof of Concept

1. User calls `sign(request)` — a yield-resume promise is created and the request is stored in `pending_signature_requests`.
2. MPC nodes begin computing the threshold signature off-chain.
3. A single participant (or a Byzantine node below threshold) calls `verify_tee()`. Some participant's attestation has expired and kicking them out would drop the participant count below the threshold relation — `accept_requests` is set to `false`.
4. MPC nodes complete the computation and call `respond(request, signature)`. The call hits the guard at line 579–581 and returns `TeeError::TeeValidationFailed`. The yield is never resumed.
5. The NEAR yield-resume timeout fires. `return_signature_and_clean_state_on_success` receives `Err(_)`, pops the queue entry, and chains a call to `fail_on_timeout`.
6. The user's suspended transaction is aborted with `RequestError::Timeout`. Their cross-chain operation fails.

### Citations

**File:** crates/contract/src/lib.rs (L153-155)
```rust
    pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
    pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
```

**File:** crates/contract/src/lib.rs (L299-302)
```rust
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

**File:** crates/contract/src/lib.rs (L1733-1738)
```rust
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

**File:** crates/contract/src/lib.rs (L2341-2344)
```rust
    pub fn fail_on_timeout() {
        // To stay consistent with the old version of the timeout error
        env::panic_str(&RequestError::Timeout.to_string());
    }
```
