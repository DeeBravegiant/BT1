### Title
Stale Governance Votes from Removed Participants Persist After Resharing Due to Unreliable Cleanup Promises — (File: `crates/contract/src/lib.rs`)

---

### Summary

After a resharing event, `vote_reshared` spawns multiple **detached** cleanup promises to remove stale votes from removed participants. The codebase itself acknowledges this cleanup can fail — a comment at the `REMOVE_NON_PARTICIPANT_UPDATE_VOTES` spawn explicitly states `vote_update` uses runtime filtering as a fallback. However, the analogous governance vote maps — `rpc_whitelist.votes`, `tee_state.votes`, and `tee_verifier_votes` — have **no such fallback filter**. If their cleanup promises fail, stale votes from removed participants remain in storage and count toward governance thresholds, breaking the invariant that only current participants' votes matter.

---

### Finding Description

In `vote_reshared` (`lib.rs:1161`), after resharing concludes, the contract spawns six detached cleanup promises:

```
REMOVE_NON_PARTICIPANT_UPDATE_VOTES   ← has fallback filter in vote_update
CLEAN_TEE_STATUS                      ← no fallback filter
CLEAN_INVALID_ATTESTATIONS
CLEANUP_ORPHANED_NODE_MIGRATIONS
CLEAN_FOREIGN_CHAIN_DATA              ← no fallback filter (covers rpc_whitelist.votes)
REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES ← no fallback filter
```

The comment at line 1176 reads:

> "Note: MpcContract::vote_update uses filtering to ensure correctness even if this cleanup fails."

This is an explicit developer acknowledgment that detached cleanup promises **can fail** and that `vote_update` has a compensating filter. Inspecting `vote_update_foreign_chain_providers` (`lib.rs:1573`), `clean_tee_status` (`lib.rs:1807`), and `remove_non_participant_tee_verifier_votes` (`lib.rs:1902`) reveals **no equivalent runtime filter** — they rely entirely on the cleanup promise executing successfully.

The `clean_foreign_chain_data` function (`lib.rs:1847`) removes stale entries via:

```rust
self.foreign_chains.get_mut().rpc_whitelist.votes.retain(participants);
```

If this detached promise fails (e.g., gas budget misconfigured via `self.config`, or contract state is large enough to exhaust the budget), the stale votes remain. When a current participant subsequently calls `vote_update_foreign_chain_providers`, the vote-counting logic receives `threshold_parameters` but the stored map still contains the removed participant's entry, potentially satisfying the threshold with fewer live participants than required.

By contrast, `add_domains_votes` is correctly handled: `RunningContractState::new` (`state/running.rs:48`) calls `get_remaining_votes(parameters.participants())` at state-transition time, filtering stale entries before they can ever be counted. `parameters_votes` is reset to `ThresholdParametersVotes::default()` at the same point. The inconsistency is that `rpc_whitelist.votes`, `tee_state.votes`, and `tee_verifier_votes` receive no equivalent at-transition filtering.

---

### Impact Explanation

If the `CLEAN_FOREIGN_CHAIN_DATA` cleanup promise fails after a resharing that removes participant P1:

- P1's prior vote for a foreign-chain RPC provider remains in `rpc_whitelist.votes`.
- A single new participant P2 voting for the same provider may now satisfy the threshold (e.g., threshold = 2, P1's stale vote + P2's fresh vote = 2).
- A malicious or compromised RPC provider is whitelisted with sub-threshold live-participant support.
- Subsequent `request_verify_foreign_tx` calls route through that provider, enabling forged foreign-chain verification results and potentially invalid bridge execution.

For `tee_state.votes` and `tee_verifier_votes`, the analogous impact is unauthorized approval of a malicious node image hash or TEE verifier contract, weakening the attestation chain that gates participant admission.

This breaks the production safety invariant: **governance threshold checks must count only current participants' votes**.

---

### Likelihood Explanation

The cleanup gas budgets are operator-configurable fields in `self.config`. A misconfigured or under-budgeted value causes the detached promise to fail silently (detached promises do not propagate failure to the parent receipt). The developers already identified this failure mode for `vote_update` and added a compensating filter; the absence of the same pattern for the other three vote maps is a design inconsistency that leaves a latent accounting divergence — directly analogous to the external report's admin-fee array not accounting for rebases.

---

### Recommendation

Apply the same defense-in-depth pattern used for `proposed_updates`: add a runtime filter inside `vote_update_foreign_chain_providers`, the TEE-status vote path, and the TEE-verifier vote path that discards stored entries whose participant IDs are no longer in the current `threshold_parameters.participants()` set before counting toward the threshold. This makes correctness independent of whether the cleanup promise executed, matching the explicit design intent already documented for `vote_update`.

Alternatively, at minimum, add a comment to each affected vote function noting that stored votes may include stale entries from removed participants if the post-resharing cleanup promise failed, so that future maintainers do not assume the stored map is always clean.

---

### Proof of Concept

1. Contract is running with participants `{P1, P2, P3}`, threshold = 2.
2. P1 calls `vote_update_foreign_chain_providers` with a malicious provider entry; vote is stored in `rpc_whitelist.votes`.
3. Resharing removes P1; new participant set is `{P2, P3, P4}`, threshold = 2.
4. `vote_reshared` transitions state and spawns `CLEAN_FOREIGN_CHAIN_DATA` as a detached promise with an under-budgeted gas value from `self.config`.
5. The detached promise fails silently; P1's entry remains in `rpc_whitelist.votes`.
6. P2 calls `vote_update_foreign_chain_providers` with the same malicious provider. The vote function sees P1's stale entry + P2's fresh entry = 2 votes, satisfying threshold = 2.
7. The malicious provider is applied (`applied` is non-empty), and `recompute_available_foreign_chains` activates it.
8. Subsequent `request_verify_foreign_tx` calls use the malicious provider, enabling forged foreign-chain verification. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L1161-1184)
```rust
    pub fn vote_reshared(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!(
            "vote_reshared: signer={}, resharing_id={:?}",
            env::signer_account_id(),
            key_event_id,
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
            // Resharing has concluded, transition to running state
            self.protocol_state = new_state;
            self.recompute_available_foreign_chains();

            // Spawn a promise to clean up votes from non-participants.
            // Note: MpcContract::vote_update uses filtering to ensure correctness even if this cleanup fails.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::REMOVE_NON_PARTICIPANT_UPDATE_VOTES.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.remove_non_participant_update_votes_tera_gas),
                )
                .detach();
```

**File:** crates/contract/src/lib.rs (L1215-1235)
```rust
            // Spawn a promise to clean up foreign chain data for non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_FOREIGN_CHAIN_DATA.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_foreign_chain_data_tera_gas),
                )
                .detach();
            // Spawn a promise to drop verifier-change votes cast by non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(
                        self.config
                            .remove_non_participant_tee_verifier_votes_tera_gas,
                    ),
                )
                .detach();
```

**File:** crates/contract/src/lib.rs (L1572-1609)
```rust
    #[handle_result]
    pub fn vote_update_foreign_chain_providers(
        &mut self,
        #[serializer(borsh)] votes: near_mpc_bounded_collections::NonEmptyBTreeMap<
            dtos::ForeignChain,
            dtos::ChainEntry,
        >,
    ) -> Result<Vec<dtos::ForeignChain>, Error> {
        let batch_hash = env::sha256_array(
            borsh::to_vec(&votes).expect("borsh serialization of votes batch must succeed"),
        );
        log!(
            "vote_update_foreign_chain_providers: signer={}, n_votes={}, batch_hash={}",
            env::signer_account_id(),
            votes.len(),
            hex::encode(batch_hash),
        );
        self.voter_or_panic();

        let threshold_parameters = self
            .protocol_state
            .threshold_parameters()
            .expect("voter_or_panic() above already errors on NotInitialized");

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let applied = self.foreign_chains.get_mut().rpc_whitelist.vote(
            participant,
            votes,
            threshold_parameters,
        )?;
        log!(
            "vote_update_foreign_chain_providers: applied chains={:?}",
            applied,
        );
        if !applied.is_empty() {
            self.recompute_available_foreign_chains();
        }
        Ok(applied)
```

**File:** crates/contract/src/lib.rs (L1843-1895)
```rust
    /// Private endpoint to clean up foreign chain policy votes and node configurations
    /// for non-participants after resharing.
    #[private]
    #[handle_result]
    pub fn clean_foreign_chain_data(&mut self) -> Result<(), Error> {
        log!(
            "clean_foreign_chain_data: signer={}",
            env::signer_account_id()
        );

        let participants = match &self.protocol_state {
            ProtocolContractState::Running(state) => state.parameters.participants(),
            _ => {
                return Err(InvalidState::ProtocolStateNotRunning.into());
            }
        };

        let participant_accounts: std::collections::HashSet<dtos::AccountId> = participants
            .participants()
            .iter()
            .map(|(account_id, _, _)| account_id.clone())
            .collect();

        let active_tls_keys: std::collections::BTreeSet<dtos::Ed25519PublicKey> = participants
            .participants()
            .iter()
            .map(|(_, _, info)| info.tls_public_key.clone())
            .collect();

        let non_participant_configs: Vec<dtos::AccountId> = self
            .node_foreign_chain_support
            .foreign_chain_support_by_node
            .keys()
            .filter(|account| !participant_accounts.contains(*account))
            .cloned()
            .collect();
        for account in &non_participant_configs {
            self.node_foreign_chain_support
                .foreign_chain_support_by_node
                .remove(account);
        }

        self.foreign_chains
            .get_mut()
            .remove_stale_configs(&active_tls_keys);

        self.foreign_chains
            .get_mut()
            .rpc_whitelist
            .votes
            .retain(participants);

        Ok(())
```

**File:** crates/contract/src/state/running.rs (L48-64)
```rust
    pub fn new(
        domains: DomainRegistry,
        keyset: Keyset,
        parameters: ThresholdParameters,
        add_domains_votes: AddDomainsVotes,
    ) -> Self {
        let remaining_add_domain_votes =
            add_domains_votes.get_remaining_votes(parameters.participants());
        RunningContractState {
            domains,
            keyset,
            parameters,
            parameters_votes: ThresholdParametersVotes::default(),
            add_domains_votes: remaining_add_domain_votes,
            previously_cancelled_resharing_epoch_id: None,
        }
    }
```

**File:** crates/contract/src/primitives/domain.rs (L268-281)
```rust
    /// Filters out existing votes no longer in the participant set
    pub fn get_remaining_votes(&self, participants: &Participants) -> Self {
        let remaining_votes = self
            .proposal_by_account
            .iter()
            .filter(|&(participant_id, _vote)| {
                participants.is_participant_given_participant_id(&participant_id.get())
            })
            .map(|(participant_id, vote)| (participant_id.clone(), vote.clone()))
            .collect();
        AddDomainsVotes {
            proposal_by_account: remaining_votes,
        }
    }
```
