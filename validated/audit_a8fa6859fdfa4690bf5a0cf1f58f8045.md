### Title
`accept_requests` Flag Blocks Fulfillment of Already-Pending Signature Requests When TEE Validation Fails - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `accept_requests` boolean flag, intended to gate **new** user requests when TEE validation fails, is also enforced inside `respond`, `respond_ckd`, and `respond_verify_foreign_tx`. This means that when the flag is set to `false`, MPC nodes cannot deliver signatures for requests that were already accepted and are sitting in the pending queue, permanently blocking those requests until the NEAR yield-resume timeout (~200 blocks) fires and returns an error to the caller.

---

### Finding Description

`MpcContract` stores an `accept_requests: bool` field. [1](#0-0) 

The flag is set to `false` inside `verify_tee` when TEE validation degrades to the point where kicking out invalid participants would break the signing threshold: [2](#0-1) 

The flag is correctly checked in `check_request_preconditions`, which gates all three user-facing submission methods (`sign`, `request_app_private_key`, `verify_foreign_transaction`): [3](#0-2) 

However, the **same flag is also checked** inside the three node-facing response methods:

- `respond` (line 579–581): [4](#0-3) 

- `respond_ckd` (line 662–664): [5](#0-4) 

- `respond_verify_foreign_tx` (line 711–713): [6](#0-5) 

These three functions are the only path through which a pending yield-resume request can be resolved before the NEAR runtime's ~200-block timeout fires. Blocking them when `accept_requests = false` means every request that was already enqueued — and for which MPC nodes have already computed a valid signature — cannot be delivered. The yield expires, the caller receives an error, and the cross-chain operation fails.

The check is also redundant from a security standpoint: `respond` already (a) requires the caller to be an attested participant via `assert_caller_is_attested_participant_and_protocol_active`, (b) checks `protocol_state.is_running_or_resharing()`, and (c) cryptographically verifies the submitted signature against the stored public key before calling `resolve_yields_for`. A compromised node cannot forge a valid signature through `respond` regardless of the `accept_requests` flag. [7](#0-6) 

---

### Impact Explanation

Any request that was accepted and enqueued before `accept_requests` was set to `false` is stuck. The NEAR yield-resume mechanism fires a timeout error after approximately 200 blocks (~4 minutes). During that window — and for as long as the TEE-degraded state persists — no pending signature, CKD, or foreign-chain verification request can be fulfilled. For users who submitted a `verify_foreign_transaction` request as part of a bridge flow where they have already committed assets on the foreign chain, the failed verification means the bridge step cannot complete. The request-lifecycle invariant — that an accepted request will eventually receive a response — is broken. This maps to the **Medium** allowed impact: *request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants*.

---

### Likelihood Explanation

`verify_tee` is callable by any current participant and is expected to be called periodically by MPC nodes. The `accept_requests = false` branch is reached whenever the surviving attested-participant set would violate the threshold relation — a realistic operational condition (e.g., several nodes fail to renew their TEE attestations simultaneously). No attacker collusion above the threshold is required; a single participant calling `verify_tee` at the right moment triggers the state. Once triggered, every in-flight request is blocked until the state is manually resolved.

---

### Recommendation

Remove the `accept_requests` guard from `respond`, `respond_ckd`, and `respond_verify_foreign_tx`. The flag should only gate **new** request submissions. Responses to already-pending requests should always be processable, because:

1. The caller is already required to be an attested participant.
2. The submitted signature is cryptographically verified before any state mutation.
3. Blocking responses provides no additional security while breaking the request lifecycle.

```rust
// In respond / respond_ckd / respond_verify_foreign_tx — REMOVE:
if !self.accept_requests {
    return Err(TeeError::TeeValidationFailed.into());
}
```

---

### Proof of Concept

1. User calls `sign(request)` with a valid 1 yoctoNEAR deposit. The contract is in `Running` state with `accept_requests = true`. The request is enqueued as a yield-resume promise. [8](#0-7) 

2. Before MPC nodes can call `respond`, a participant calls `verify_tee`. TEE validation finds that the surviving attested-participant set is below the threshold bound, so `accept_requests` is set to `false`. [9](#0-8) 

3. MPC nodes finish computing the threshold signature and call `respond(request, valid_signature)`. The function hits the guard at line 579 and returns `Err(TeeError::TeeValidationFailed)` — the valid signature is discarded. [4](#0-3) 

4. After ~200 NEAR blocks the runtime fires the yield-resume timeout. `return_signature_and_clean_state_on_success` receives `Err(PromiseError::Failed)` and the caller's `sign` call resolves with an error. The user's cross-chain operation fails and must be retried from scratch once the MPC network recovers.

### Citations

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

**File:** crates/contract/src/lib.rs (L392-397)
```rust
        self.enqueue_yield_request(
            method_names::RETURN_SIGNATURE_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_signature_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L573-577)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
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

**File:** crates/contract/src/lib.rs (L1733-1738)
```rust
                        "Kicking out participants with an invalid TEE status would break the threshold relation ({:?}); {} participants remain with a valid TEE status. This requires manual intervention. We will not accept new signature requests as a safety precaution.",
                        err,
                        remaining,
                    );
                    self.accept_requests = false;
                    return Ok(false);
```
