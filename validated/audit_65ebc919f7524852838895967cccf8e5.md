### Title
No Mechanism to Immediately Revoke a Compromised `NodeImageHash` Breaks TEE Participant-State Invariant - (File: `crates/contract/src/lib.rs`)

### Summary
The MPC contract's `vote_code_hash` adds a `NodeImageHash` to the TEE allowlist but provides no `vote_remove_code_hash` counterpart. A hash can only be expired passively — by voting in a successor and waiting out the 7-day grace period (`tee_upgrade_deadline_duration`). If no successor is voted in, the compromised hash is **never** removed: the `valid_entries` fallback always returns at least one entry. During the entire exposure window, nodes running the compromised image pass attestation checks and remain active participants, breaking the TEE security invariant.

### Finding Description
`vote_code_hash` at `crates/contract/src/lib.rs:1407` calls `tee_state.whitelist_tee_proposal(code_hash, ...)` once the signing threshold is reached. [1](#0-0) 

The expiry logic in `AllowedDockerImageHashes::valid_entries` at `crates/contract/src/tee/proposal.rs:170` computes a `cutoff_index` based on which entries have a successor whose grace-period deadline has passed. [2](#0-1) 

Two structural problems follow directly from this design:

1. **Single-hash case — permanent retention.** If only one hash exists, `rposition` finds no entry satisfying `grace_period_deadline < current_time` and returns `None`, so `cutoff_index` defaults to `0`. The slice `get(0..)` returns the entire list — the single entry is always returned and can never be evicted, even after it is known to be compromised.

2. **Multi-hash case — mandatory 7-day window.** If a successor is voted in, the old hash survives for the full `tee_upgrade_deadline_duration` (default 7 days, configurable via `config.tee_upgrade_deadline_duration_seconds`). [3](#0-2) 

There is no `vote_remove_code_hash` in the MPC contract. A grep across the entire repository confirms the function exists only in documentation for the separate HOT TEE governance contract, not in the production MPC contract code. The operator guide explicitly states: *"There is no `vote_remove_code_hash`. Once a successor hash is voted in, the previous hash remains valid for a 7-day grace period and then auto-expires."* [4](#0-3) 

The HOT TEE governance contract design does expose `vote_remove_code_hash`, confirming the gap is a known asymmetry — present for HOT but absent for MPC. [5](#0-4) 

By contrast, `vote_remove_launcher_hash` and `vote_remove_os_measurement` both exist in the MPC contract and require unanimity, demonstrating that the protocol designers have a working pattern for immediate revocation — it was simply never applied to `NodeImageHash`. [6](#0-5) 

### Impact Explanation
If a `NodeImageHash` is compromised after being voted in (supply-chain attack on the Docker image, a discovered backdoor, or a critical vulnerability in the MPC node binary), the contract has no path to immediately invalidate it. Nodes running the compromised image:

- Pass `submit_participant_info` attestation checks because the hash remains in the allowlist.
- Remain active participants eligible to receive signing assignments, CKD rounds, and foreign-chain verification work.
- Can participate in threshold signing rounds while the backdoor is active.

This breaks the production safety invariant that only nodes running approved, uncompromised images may participate. The participant-state is corrupted: the contract treats compromised nodes as legitimate, matching the **Medium** impact class — *participant-state manipulation that breaks production safety/accounting invariants*.

### Likelihood Explanation
Supply-chain attacks on Docker images are a documented, realistic threat vector for containerized infrastructure. A threshold of honest participants can vote in a hash in good faith, and a vulnerability or backdoor may only be discovered after deployment. The minimum 7-day exposure window (or indefinite exposure if no successor hash is voted in) is a meaningful operational risk for a production MPC network controlling real funds on mainnet.

### Recommendation
Add a `vote_remove_code_hash(code_hash: NodeImageHash)` function requiring **all** participants to vote — matching the unanimity bar already used by `vote_remove_launcher_hash` and `vote_remove_os_measurement`. This mirrors the pattern present in the HOT TEE governance contract and allows immediate revocation of a compromised hash without waiting for the passive grace-period mechanism. Additionally, the `valid_entries` fallback that always retains the last entry should be reviewed: if the last remaining hash is known-compromised, the fallback actively prevents removal. [7](#0-6) 

### Proof of Concept
1. Participants call `vote_code_hash(hash_A)` — threshold reached, `hash_A` added to `AllowedDockerImageHashes`.
2. A supply-chain attack is discovered: `hash_A` contains a backdoor.
3. **Scenario A (successor voted):** Honest participants call `vote_code_hash(hash_B)`. `hash_A` remains in `valid_entries` for the full 7-day `tee_upgrade_deadline_duration`.
4. **Scenario B (no successor):** No new hash is voted in. `hash_A` is the only entry; `valid_entries` always returns it via the `unwrap_or(0)` fallback. `hash_A` is never removed.
5. In both scenarios, nodes running `hash_A` call `submit_participant_info` — attestation passes because `hash_A` is still in the allowlist.
6. These nodes are accepted as active participants and assigned signing work.
7. The backdoor can exfiltrate key-share material or manipulate signing outputs during the entire exposure window, breaking the TEE participant-state invariant. [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L1406-1431)
```rust
    #[handle_result]
    pub fn vote_code_hash(&mut self, code_hash: NodeImageHash) -> Result<(), Error> {
        log!(
            "vote_code_hash: signer={}, code_hash={:?}",
            env::signer_account_id(),
            code_hash,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let votes = self.tee_state.vote(code_hash, &participant);

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        // If the vote threshold is met and the new Docker hash is allowed by the TEE's RTMR3,
        // update the state
        if votes >= self.threshold()?.value() {
            self.tee_state
                .whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
        }

        Ok(())
    }
```

**File:** crates/contract/src/lib.rs (L1467-1495)
```rust
    /// Vote to remove a launcher image hash from the allowed set. Requires ALL participants
    /// to vote for removal, since this invalidates attestations of nodes running that launcher.
    #[handle_result]
    pub fn vote_remove_launcher_hash(
        &mut self,
        launcher_hash: LauncherImageHash,
    ) -> Result<(), Error> {
        log!(
            "vote_remove_launcher_hash: signer={}, launcher_hash={:?}",
            env::signer_account_id(),
            launcher_hash,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let action = LauncherVoteAction::Remove(launcher_hash);
        let votes = self.tee_state.vote_launcher(action, &participant);

        // Removal requires ALL participants to vote
        let total_participants = threshold_parameters.participants().len() as u64;
        if votes >= total_participants {
            let removed = self.tee_state.remove_launcher_image(&launcher_hash);
            log!("launcher hash remove result: {}", removed);
        }

        Ok(())
    }
```

**File:** crates/contract/src/tee/proposal.rs (L170-193)
```rust
    fn valid_entries(&self, tee_upgrade_deadline_duration: Duration) -> Vec<AllowedMpcDockerImage> {
        let current_time = Timestamp::now();
        // get the index of the most recently enforced docker image
        let cutoff_index = self
            .allowed_tee_proposals
            .iter()
            .rposition(|allowed_docker_image| {
                let Some(grace_period_deadline) = allowed_docker_image
                    .added
                    .checked_add(tee_upgrade_deadline_duration)
                else {
                    log!("Error: timestamp overflowed when calculating grace_period_deadline.");
                    return true;
                };
                // if the grace period for this docker hash is in the past, then older hashes are no longer accepted
                grace_period_deadline < current_time
            })
            .unwrap_or(0);

        self.allowed_tee_proposals
            .get(cutoff_index..)
            .unwrap_or(&[])
            .to_vec()
    }
```

**File:** crates/contract/src/tee/proposal.rs (L197-200)
```rust
    pub fn cleanup_expired_hashes(&mut self, tee_upgrade_deadline_duration: Duration) {
        let valid_entries = self.valid_entries(tee_upgrade_deadline_duration);
        self.allowed_tee_proposals = valid_entries;
    }
```

**File:** crates/contract/src/tee/proposal.rs (L233-246)
```rust
    /// Returns valid hashes without cleaning expired entries (read-only). Ensures that at least
    /// one proposal (the latest) is always returned. Use [`Self::cleanup_expired_hashes`]
    /// explicitly when cleanup of the internal structure is needed.
    pub fn get(&self, tee_upgrade_deadline_duration: Duration) -> Vec<AllowedMpcDockerImage> {
        self.valid_entries(tee_upgrade_deadline_duration)
    }

    /// Returns only the image hashes of valid entries.
    pub fn get_image_hashes(&self, tee_upgrade_deadline_duration: Duration) -> Vec<NodeImageHash> {
        self.valid_entries(tee_upgrade_deadline_duration)
            .into_iter()
            .map(|entry| entry.image_hash)
            .collect()
    }
```

**File:** docs/running-an-mpc-node-in-tdx-external-guide.md (L1619-1619)
```markdown
> **Note:** There is no `vote_remove_code_hash`. Once a successor hash is voted in, the previous hash remains valid for a 7-day grace period and then auto-expires — so unlike launcher and OS-measurement voting there is no explicit remove command.
```

**File:** docs/hot-tee-signing-design.md (L398-398)
```markdown
| `vote_remove_code_hash(code_hash)` | Call | Governor | Vote to remove a Docker image hash before natural expiry |
```
