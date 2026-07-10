### Title
Pending Sign/CKD/ForeignTx Requests Are Permanently Blocked When `accept_requests = false`, Causing Forced Timeouts — (`crates/contract/src/lib.rs`)

### Summary

When `verify_tee()` sets `self.accept_requests = false` because fewer than threshold participants hold valid TEE attestations, the `respond()`, `respond_ckd()`, and `respond_verify_foreign_tx()` functions all gate on the same flag and unconditionally reject every response attempt. This means MPC nodes that have already computed a valid threshold signature for an in-flight request cannot deliver it to the contract. The request sits in `pending_signature_requests` until the NEAR yield-resume runtime fires the ~200-block timeout, at which point `return_signature_and_clean_state_on_success` pops the yield and calls `fail_on_timeout`, permanently failing the user's cross-chain operation. The 1 yoctoNEAR deposit is refunded, but the signed payload is lost and the user's foreign-chain transaction cannot be completed.

### Finding Description

`respond()` enforces the `accept_requests` flag at line 579–581:

```rust
if !self.accept_requests {
    return Err(TeeError::TeeValidationFailed.into());
}
```

The identical guard appears in `respond_ckd()` (lines 662–664) and `respond_verify_foreign_tx()` (lines 711–713).

`verify_tee()` sets the flag to `false` at lines 1737–1738 when kicking out participants with expired attestations would leave fewer than the governance threshold with valid attestations:

```rust
self.accept_requests = false;
return Ok(false);
```

The flag is only restored to `true` by a subsequent successful `verify_tee()` call that finds a full or partial-but-above-threshold valid set. Re-attestation via `submit_participant_info` uses exponential backoff up to 12 hours, and the periodic attestation task runs every 7 days. The NEAR yield-resume timeout is ~200 blocks (~4 minutes on mainnet). Any request submitted before `accept_requests` was cleared will time out before re-attestation completes.

The `respond()` function already independently enforces two security invariants that make the `accept_requests` gate redundant for response delivery:

1. `assert_caller_is_attested_participant_and_protocol_active()` (line 573) — only an attested participant in the active phase can call `respond()`.
2. Cryptographic signature verification (lines 586–640) — the contract verifies the ECDSA/EdDSA signature against the derived public key; an invalid signature is rejected regardless.

The `accept_requests` flag therefore adds no security value to `respond()` — it only prevents delivery of signatures that were legitimately computed by a threshold quorum before the TEE degradation event.

### Impact Explanation

Any sign, CKD, or foreign-transaction verification request that is in-flight at the moment `verify_tee()` sets `accept_requests = false` is permanently unserviceable. The MPC nodes hold a valid threshold signature but cannot submit it. After ~200 blocks the yield-resume timeout fires, `fail_on_timeout` panics, and the user's cross-chain operation is irrecoverably failed. This breaks the request-lifecycle safety invariant: a request accepted by the contract and processed by a threshold quorum of honest nodes must be completable. No privileged role — not even the full participant set — can override the block, because `respond()` has no admin bypass path.

This maps to the **Medium** allowed impact: *request-lifecycle manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.*

### Likelihood Explanation

The `accept_requests = false` state is a normal operational outcome during image-hash rotation (the 7-day grace period) or when attestation certificates expire. Any single participant can call `verify_tee()` — it requires only `voter_or_panic()`, not a threshold vote. A Byzantine participant below the signing threshold can therefore trigger the state at will whenever enough peer attestations have aged out, racing against in-flight requests. The window between request submission and the ~200-block timeout is narrow (~4 minutes), but the re-attestation backoff (up to 12 hours) guarantees the flag stays `false` far longer than the yield window.

### Recommendation

Remove the `!self.accept_requests` guard from `respond()`, `respond_ckd()`, and `respond_verify_foreign_tx()`. The flag correctly gates *new* request submission in `check_request_preconditions()` (line 300–302) — that is the right enforcement point. Response delivery for already-queued requests should be governed solely by the existing attestation check (`assert_caller_is_attested_participant_and_protocol_active`) and the cryptographic signature verification, both of which are already present and sufficient.

### Proof of Concept

1. User calls `sign(payload)` at block N; the request is queued in `pending_signature_requests` with a yield that expires at block N+200.
2. MPC nodes observe the request, form a threshold quorum, and compute a valid ECDSA signature off-chain.
3. At block N+5, a single participant calls `verify_tee()`; two of three participants have expired attestations; the kickout would drop below threshold, so `accept_requests = false` is set.
4. The leader node calls `respond(request, valid_signature)`. The contract reaches line 579 and returns `Err(TeeError::TeeValidationFailed)`. The call fails; the yield is not resumed.
5. The node retries on every subsequent block. Each attempt fails identically.
6. At block N+200, the NEAR runtime fires `return_signature_and_clean_state_on_success` with `Err(PromiseError::Failed)`. The yield is popped, `fail_on_timeout` is scheduled, and the user's transaction fails with `RequestError::Timeout`.
7. The 1 yoctoNEAR deposit is refunded; the cross-chain operation is permanently lost.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L298-302)
```rust
        // 4. Refuse the request if the contract is not currently accepting requests
        //    (e.g. because TEE validation has failed).
        if !self.accept_requests {
            env::panic_str(&TeeError::TeeValidationFailed.to_string())
        }
```

**File:** crates/contract/src/lib.rs (L573-581)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L658-664)
```rust
        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L705-713)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
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

**File:** crates/contract/src/lib.rs (L2249-2271)
```rust
    pub fn return_signature_and_clean_state_on_success(
        &mut self,
        request: SignatureRequest,
        #[callback_result] signature: Result<dtos::SignatureResponse, PromiseError>,
    ) -> PromiseOrValue<dtos::SignatureResponse> {
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

**File:** crates/contract/src/lib.rs (L2340-2344)
```rust
    #[private]
    pub fn fail_on_timeout() {
        // To stay consistent with the old version of the timeout error
        env::panic_str(&RequestError::Timeout.to_string());
    }
```
