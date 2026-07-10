### Title
`accept_requests` Flag Enforced at Both Request Submission and Response Time Permanently Blocks In-Flight Requests After TEE Validation Failure — (File: `crates/contract/src/lib.rs`)

### Summary

The `accept_requests` boolean is checked at both the request-entry point (`check_request_preconditions`) and at every response-delivery exit point (`respond`, `respond_ckd`, `respond_verify_foreign_tx`). When `verify_tee()` sets `accept_requests = false` after requests are already pending in the yield queue, those in-flight requests cannot be resolved by any `respond*` call and are permanently blocked until the NEAR yield timeout fires — an exact structural analog of M-09's "blacklisted after deposit" pattern.

### Finding Description

The contract enforces `accept_requests` at two distinct lifecycle points:

**Entry — request submission** (`check_request_preconditions`): [1](#0-0) 

```rust
if !self.accept_requests {
    env::panic_str(&TeeError::TeeValidationFailed.to_string())
}
```

**Exit — response delivery** (`respond`, `respond_ckd`, `respond_verify_foreign_tx`): [2](#0-1) [3](#0-2) [4](#0-3) 

```rust
if !self.accept_requests {
    return Err(TeeError::TeeValidationFailed.into());
}
```

The flag is mutated by `verify_tee()`, callable by any single participant: [5](#0-4) 

```rust
// When kicking out participants would break the threshold relation:
self.accept_requests = false;
return Ok(false);
``` [6](#0-5) 

```rust
TeeValidationResult::Full => {
    self.accept_requests = true;
    ...
}
```

The three pending-request maps that hold in-flight yields are: [7](#0-6) 

```rust
pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
```

When `accept_requests` transitions `true → false` while any of these maps contain live yields, every subsequent `respond*` call is rejected before it can call `resolve_yields_for`. The yields can only exit via the NEAR yield-timeout path, which fires `fail_on_timeout` and panics the original caller's transaction. [8](#0-7) 

### Impact Explanation

All requests accepted while `accept_requests = true` are blocked from resolution the moment the flag flips. For `sign` and `request_app_private_key` the user loses the 1 yoctoNEAR deposit and the signature; for `verify_foreign_transaction` the impact is materially worse: the user has already submitted an irreversible foreign-chain transaction and is waiting for the MPC network to attest it. A forced timeout means the bridge operation fails while the foreign-chain funds are already committed, breaking the safety invariant that an accepted `verify_foreign_transaction` request will be resolved.

This maps to the **Medium** allowed impact: *"request-lifecycle … manipulation that breaks production safety/accounting invariants."*

### Likelihood Explanation

`verify_tee()` requires only a single participant (`voter_or_panic()`), placing it within reach of any Byzantine participant strictly below the signing threshold. TEE attestation expiry is a normal, predictable protocol event. An adversarial participant can observe when attestations are about to expire, wait for a burst of user requests to enter the pending queue, then call `verify_tee()` to flip `accept_requests = false` — maximising the number of in-flight requests that are blocked. No collusion above threshold is required. [9](#0-8) 

### Recommendation

Remove the `accept_requests` guard from `respond`, `respond_ckd`, and `respond_verify_foreign_tx`. The flag's purpose is to stop the contract from *accepting new requests*; it should not prevent resolution of requests that were already accepted. Requests already in the yield queue were validated at submission time and should be resolvable regardless of subsequent TEE-state changes. The existing timeout mechanism already handles the case where nodes never deliver a response.

### Proof of Concept

1. Contract is `Running`, `accept_requests = true`. User calls `sign()` → yield enqueued in `pending_signature_requests`.
2. One participant's TEE attestation expires. A second (Byzantine) participant calls `verify_tee()`. Kicking out the expired node would break the threshold relation → `accept_requests = false`.
3. MPC nodes finish computing the signature and call `respond(request, response)`.
4. `respond` hits the guard at line 579–581 and returns `Err(TeeError::TeeValidationFailed)` — the yield is never resolved.
5. The NEAR yield timeout fires → `return_signature_and_clean_state_on_success` receives `Err(PromiseError::Failed)` → `fail_on_timeout` panics → user's transaction fails with `RequestError::Timeout`.
6. For `verify_foreign_transaction`: the user's foreign-chain transaction is already irreversible; the bridge operation fails with no recourse.

### Citations

**File:** crates/contract/src/lib.rs (L153-155)
```rust
    pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
    pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
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

**File:** crates/contract/src/lib.rs (L1693-1696)
```rust
    pub fn verify_tee(&mut self) -> Result<bool, Error> {
        log!("verify_tee: signer={}", env::signer_account_id());
        // Caller must be a participant (node or operator).
        self.voter_or_panic();
```

**File:** crates/contract/src/lib.rs (L1709-1712)
```rust
            TeeValidationResult::Full => {
                self.accept_requests = true;
                log!("All participants have an accepted Tee status");
                Ok(true)
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

**File:** crates/contract/src/lib.rs (L2341-2344)
```rust
    pub fn fail_on_timeout() {
        // To stay consistent with the old version of the timeout error
        env::panic_str(&RequestError::Timeout.to_string());
    }
```
