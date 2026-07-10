### Title
Single Participant Can Unilaterally Halt Signing and Trigger Resharing via `verify_tee()` — (File: `crates/contract/src/lib.rs`)

---

### Summary

`verify_tee()` is documented as a TEE attestation re-validation function, but a single MPC participant (strictly below the signing threshold) can call it at any time to unilaterally halt all signing operations (`accept_requests = false`) and force the contract into `Resharing` state — state transitions that normally require threshold-level consensus. This mirrors the "FreezerAddress has more power than required" class: a role whose stated purpose is narrow (verify attestations) silently carries the power to freeze the signing pipeline and trigger a major protocol state change without any multi-party approval.

---

### Finding Description

`verify_tee()` is gated only by `self.voter_or_panic()`, which requires the caller to be **any single current participant** — no threshold vote, no multi-party agreement. [1](#0-0) 

When called, the function invokes `reverify_and_cleanup_participants` against the current participant set. Two critical side-effect branches follow:

**Branch 1 — Halt signing unilaterally:**
If the surviving valid-attestation set is too small to maintain the threshold relation, the contract sets `self.accept_requests = false`, immediately stopping all `sign`, `respond`, `request_app_private_key`, and `respond_ckd` calls for every user of the MPC network. [2](#0-1) 

**Branch 2 — Trigger resharing unilaterally:**
If the surviving set is large enough, the function calls `transition_to_resharing_no_checks()` — bypassing the normal `process_new_parameters_proposal` validation path — and transitions the contract into `Resharing` state. [3](#0-2) 

The function that is bypassed is named explicitly: [4](#0-3) 

By contrast, the normal governance path for resharing (`vote_new_parameters`) requires **threshold-many participants** to agree on the same proposal before any state transition occurs: [5](#0-4) 

Neither side effect emits an on-chain event. The halt of signing produces only a `log!` message; the resharing transition produces no notification at all. Users whose pending `sign` requests are now blocked have no on-chain signal. [6](#0-5) 

---

### Impact Explanation

**Medium — contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.**

A Byzantine participant (strictly below the signing threshold) can:

1. Monitor the blockchain for any participant whose 7-day attestation window has lapsed but who has not yet renewed.
2. Call `verify_tee()` in that window to either (a) set `accept_requests = false`, freezing all pending and future signing requests for every user, or (b) force the contract into `Resharing` state, blocking signing until all new participants complete the resharing protocol.

All pending `sign` / `request_app_private_key` / `verify_foreign_transaction` yield-promises are left unresolvable while `accept_requests` is false or while the contract is in `Resharing`. Users' 1-yoctoNEAR deposits are locked in the yield queue with no recourse until the network recovers. This breaks the request-lifecycle safety invariant: a submitted and paid-for signing request must eventually be fulfilled or refunded, but the contract provides no automatic refund path when `accept_requests` is toggled off by a single actor.

---

### Likelihood Explanation

**Medium.** MPC node attestations expire every 7 days by design. [7](#0-6) 

In any production network with rolling upgrades, operator downtime, or network partitions, there will be windows where one or more participants' attestations have lapsed but not yet been renewed. A Byzantine participant needs only to observe this condition on-chain (via `get_tee_accounts()`, a public view method) and submit a single transaction. No key material, no collusion, no threshold is required.

---

### Recommendation

1. **Require threshold votes to trigger resharing via `verify_tee()`**, consistent with the governance model used by `vote_new_parameters`. A single participant should be able to *report* expired attestations but not unilaterally *act* on them.
2. **Emit an on-chain event** (not just a `log!`) when `accept_requests` is set to `false` or when resharing is triggered via `verify_tee()`, so that users with pending requests can observe the state change and take action.
3. **Provide a refund path** for pending yield-requests when `accept_requests` is toggled off, analogous to the timeout-based cleanup already present for the verifier-unreachable case.

---

### Proof of Concept

**Setup:** 3-participant network, threshold 2. Participant C's attestation expires (7-day window lapses). Participant A is Byzantine.

**Attack:**
1. Participant A observes via `get_tee_accounts()` that C's attestation is expired.
2. Participant A calls `verify_tee()` as a single transaction.
3. `reverify_and_cleanup_participants` returns `TeeValidationResult::Partial { participants_with_valid_attestation: [A, B] }`.
4. The threshold relation check passes (2 remaining ≥ threshold 2).
5. `transition_to_resharing_no_checks` is called — the contract enters `Resharing` state.
6. All pending `sign` requests are now blocked. Users' deposits are locked.
7. Participant C (the honest node that was temporarily offline) cannot recover without completing the full resharing protocol.

This is confirmed by the existing sandbox test, which demonstrates exactly this flow triggered by a single participant: [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L1693-1696)
```rust
    pub fn verify_tee(&mut self) -> Result<bool, Error> {
        log!("verify_tee: signer={}", env::signer_account_id());
        // Caller must be a participant (node or operator).
        self.voter_or_panic();
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

**File:** crates/contract/src/lib.rs (L1760-1764)
```rust
                let proposed_parameters =
                    ProposedThresholdParameters::new(threshold_parameters, BTreeMap::new());
                let res = running_state.transition_to_resharing_no_checks(&proposed_parameters);
                if let Some(resharing) = res {
                    self.protocol_state = ProtocolContractState::Resharing(resharing);
```

**File:** crates/contract/src/state/running.rs (L66-102)
```rust
    pub fn transition_to_resharing_no_checks(
        &mut self,
        proposal: &ProposedThresholdParameters,
    ) -> Option<ResharingContractState> {
        if let Some(first_domain) = self.domains.get_domain_by_index(0) {
            let epoch_id = self.prospective_epoch_id();

            Some(ResharingContractState {
                previous_running_state: RunningContractState::new(
                    self.domains.clone(),
                    self.keyset.clone(),
                    self.parameters.clone(),
                    self.add_domains_votes.clone(),
                ),
                reshared_keys: Vec::new(),
                resharing_key: KeyEvent::new(
                    epoch_id,
                    first_domain.clone(),
                    proposal.parameters().clone(),
                ),
                cancellation_requests: HashSet::new(),
                per_domain_thresholds: proposal.per_domain_thresholds().clone(),
            })
        } else {
            // New parameters were proposed, but we have no keys, so directly
            // transition into Running state but bump the EpochId. With no
            // domains the per-domain threshold updates have nothing to apply to
            // and are dropped.
            *self = RunningContractState::new(
                self.domains.clone(),
                Keyset::new(self.keyset.epoch_id.next(), Vec::new()),
                proposal.parameters().clone(),
                self.add_domains_votes.clone(),
            );
            None
        }
    }
```

**File:** crates/contract/src/state/running.rs (L104-125)
```rust
    /// Casts a vote for `proposal` to the current state, propagating any errors.
    /// Returns ResharingContractState if the proposal is accepted.
    pub fn vote_new_parameters(
        &mut self,
        prospective_epoch_id: EpochId,
        proposal: &ProposedThresholdParameters,
    ) -> Result<Option<ResharingContractState>, Error> {
        let expected_prospective_epoch_id = self.prospective_epoch_id();

        if prospective_epoch_id != expected_prospective_epoch_id {
            return Err(InvalidParameters::EpochMismatch {
                expected: expected_prospective_epoch_id,
                provided: prospective_epoch_id,
            }
            .into());
        }

        if self.process_new_parameters_proposal(proposal)? {
            return Ok(self.transition_to_resharing_no_checks(proposal));
        }
        Ok(None)
    }
```

**File:** docs/tee-lifecycle.md (L186-208)
```markdown
2. **Periodic attestation** — Every 7 days, generates a fresh TDX attestation quote and submits it to the governance contract via [`submit_participant_info()`][submit-participant-info]. Includes exponential backoff retries. (Reference: [`periodic_attestation_submission`][periodic-attestation])

3. **Monitor attestation removal** — Watches the contract for changes to the attested nodes list. If this node's attestation is removed (e.g., due to image hash rotation), resubmits immediately. (Reference: [`monitor_attestation_removal`][monitor-attestation-removal])

4. **Poll foreign chain policy** — Subscribes to the governance contract's [`get_foreign_chain_policy()`][get-foreign-chain-policy] view method via the Contract State Subscriber. Provides the active [`ForeignChainPolicy`][foreign-chain-policy-type] to consumers — for the MPC node this feeds [foreign transaction verification][foreign-tx-verification], for the Archive Signer it configures the validation SDK's RPC providers. (Reference: the MPC node currently fetches this [on-demand in the coordinator][coordinator-fcp]; the TEE Context will move it to continuous polling.)

[foreign-tx-verification]: foreign-chain-transactions.md

[foreign-chain-policy-type]: https://github.com/near/mpc/blob/ce53324f472aa89fdf702d7482211bbdb6a44967/crates/contract-interface/src/types/foreign_chain.rs#L570
[coordinator-fcp]: https://github.com/near/mpc/blob/ce53324f472aa89fdf702d7482211bbdb6a44967/crates/node/src/coordinator.rs#L378
[allowed-docker-image-hashes]: https://github.com/near/mpc/blob/ce53324f472aa89fdf702d7482211bbdb6a44967/crates/contract/src/lib.rs#L1624
[allowed-launcher-compose-hashes]: https://github.com/near/mpc/blob/ce53324f472aa89fdf702d7482211bbdb6a44967/crates/contract/src/lib.rs#L1638
[submit-participant-info]: https://github.com/near/mpc/blob/ce53324f472aa89fdf702d7482211bbdb6a44967/crates/contract/src/lib.rs#L820
[get-foreign-chain-policy]: https://github.com/near/mpc/blob/ce53324f472aa89fdf702d7482211bbdb6a44967/crates/contract/src/lib.rs#L1663

## Attestation

After boot, every service must continuously prove to the governance contract that it is running an approved image inside a genuine TDX enclave. The attestation lifecycle is the same for all three services:

1. **Initial attestation** — the service generates a TDX quote that binds its identity (TLS public key) to the enclave measurements and submits it to the governance contract.
2. **Periodic renewal** — every 7 days a fresh quote is generated and resubmitted, so the contract always holds a recent proof.
3. **Removal monitoring** — if the contract removes the node's attestation (e.g., after an image-hash rotation), the service detects this and resubmits immediately.
4. **Collective verification** — every 2 days, any participant can trigger `verify_tee()` on the governance contract to re-validate all stored attestations and evict nodes whose image hashes are no longer on the approved list.
```

**File:** crates/contract/tests/sandbox/tee.rs (L784-813)
```rust
    // Call verify_tee() to trigger resharing
    let verify_result = mpc_signer_accounts[0]
        .call(contract.id(), method_names::VERIFY_TEE)
        .args_json(serde_json::json!({}))
        .max_gas()
        .transact()
        .await?;
    dbg!(&verify_result);
    assert!(
        verify_result.is_success(),
        "verify_tee call failed: {:?}",
        verify_result
    );

    // Verify contract transitioned to Resharing state
    let state_after_verify = get_state(&contract).await;
    let prospective_epoch_id = match &state_after_verify {
        dtos::ProtocolContractState::Resharing(resharing_state) => {
            resharing_state.resharing_key.epoch_id
        }
        _ => panic!("expected Resharing state, got {:?}", state_after_verify),
    };

    // Complete resharing with the remaining participants (first 2)
    let remaining_accounts = &mpc_signer_accounts[..2];
    conclude_resharing(&contract, remaining_accounts, prospective_epoch_id).await?;

    // Verify final state: 2 participants, target removed
    let final_participants = assert_running_return_participants(&contract).await?;
    assert_eq!(final_participants.participants.len(), PARTICIPANT_COUNT - 1);
```
