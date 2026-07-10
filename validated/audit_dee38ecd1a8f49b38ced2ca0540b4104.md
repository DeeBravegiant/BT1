### Title
Missing `vote_remove_code_hash` Prevents Immediate Revocation of Compromised MPC Docker Image Hashes - (File: `crates/contract/src/lib.rs`, `crates/contract/src/tee/tee_state.rs`)

---

### Summary

The MPC contract provides `vote_code_hash` to add MPC node Docker image hashes to the TEE whitelist, but provides no corresponding `vote_remove_code_hash` governance function. Unlike launcher image hashes (`vote_remove_launcher_hash`) and OS measurements (`vote_remove_os_measurement`), which both have explicit participant-unanimous removal paths, Docker image hashes can only be passively expired via a time-based deadline. This means a compromised or deprecated MPC node image hash cannot be immediately revoked, allowing nodes running that image to continue submitting valid attestations and participating in threshold signing for the full duration of the upgrade deadline window.

---

### Finding Description

The `vote_code_hash` function allows threshold-many participants to add a new `NodeImageHash` to `allowed_docker_image_hashes`: [1](#0-0) 

When the threshold is met, `whitelist_tee_proposal` is called, which inserts the hash into `AllowedDockerImageHashes` with a deadline timestamp: [2](#0-1) 

The `TeeState` struct holds `allowed_docker_image_hashes`, `allowed_launcher_images`, and `allowed_measurements` as three parallel whitelists: [3](#0-2) 

For launcher image hashes, an explicit `vote_remove_launcher_hash` exists requiring unanimity: [4](#0-3) 

For OS measurements, an explicit `vote_remove_os_measurement` exists requiring unanimity: [5](#0-4) 

No analogous `vote_remove_code_hash` function exists anywhere in the contract source. A `grep` across the entire repository for `vote_remove_code_hash` or `remove_code_hash` returns only documentation references, not any contract implementation. The only removal mechanism for Docker image hashes is the passive `cleanup_expired_hashes` call, which is time-gated by `tee_upgrade_deadline_duration_seconds` from the contract config: [6](#0-5) 

This asymmetry is confirmed by the method name registry, which lists `vote_remove_launcher_hash` and `vote_remove_os_measurement` but has no `vote_remove_code_hash` entry: [7](#0-6) 

---

### Impact Explanation

When a node submits its attestation via `submit_participant_info`, the contract verifies the attestation against `get_allowed_mpc_docker_image_hashes`, which returns all non-expired hashes: [8](#0-7) 

If a whitelisted MPC Docker image is found to be compromised (e.g., it leaks key shares, produces biased signatures, or allows signing bypass), participants have no governance path to immediately revoke it. Nodes running the compromised image will continue to pass attestation verification and remain eligible participants in threshold signing rounds for the entire `tee_upgrade_deadline_duration` window. This breaks the production safety invariant of the TEE governance system: the whitelist is supposed to be the authoritative control plane for which node software is trusted to hold key shares and co-sign transactions. The inability to immediately revoke a hash means the contract cannot enforce the security boundary it is designed to provide.

This maps to the **Medium** allowed impact: *participant-state or contract execution-flow manipulation that breaks production safety/accounting invariants*.

---

### Likelihood Explanation

The operational guide explicitly documents the expected workflow of removing old hashes after upgrades: [9](#0-8) 

This confirms the removal of old image hashes is a planned, routine operation. The absence of a `vote_remove_code_hash` function means this operation is impossible for MPC Docker image hashes specifically, while it is possible for launcher hashes and OS measurements. Any security incident requiring immediate revocation of a Docker image hash — a realistic scenario in a production MPC network — would be unserviceable until the passive expiry fires.

---

### Recommendation

Add a `vote_remove_code_hash` function mirroring `vote_remove_launcher_hash` and `vote_remove_os_measurement`. It should require unanimity (all participants vote) to match the security posture of the other removal functions, and should call a new `remove_docker_image_hash` method on `TeeState` that removes the entry from `allowed_docker_image_hashes` and clears the code hash votes. An event should be emitted for transparency.

---

### Proof of Concept

1. Participants vote via `vote_code_hash(hash_A)` until threshold is reached; `hash_A` is added to `allowed_docker_image_hashes`.
2. `hash_A` is later discovered to be compromised (e.g., the image contains a backdoor that leaks key shares).
3. Participants attempt to revoke `hash_A`. There is no `vote_remove_code_hash` function — the call does not exist in the contract ABI.
4. Nodes running `hash_A` continue to call `submit_participant_info` with attestations that reference `hash_A`. The contract's `add_participant` verifies against `get_allowed_mpc_docker_image_hashes`, which still returns `hash_A` (it has not expired).
5. These nodes remain valid participants, are included in `vote_new_parameters` proposals, and participate in threshold signing rounds — all while running a known-compromised image.
6. The only exit is waiting for the passive `tee_upgrade_deadline_duration` expiry, during which the compromised nodes remain active.

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

**File:** crates/contract/src/lib.rs (L1524-1552)
```rust
    /// Vote to remove an OS measurement set from the allowed list. Requires ALL participants
    /// to vote for removal.
    #[handle_result]
    pub fn vote_remove_os_measurement(
        &mut self,
        measurement: ContractExpectedMeasurements,
    ) -> Result<(), Error> {
        log!(
            "vote_remove_os_measurement: signer={}, measurement={:?}",
            env::signer_account_id(),
            measurement,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let action = MeasurementVoteAction::Remove(measurement.clone());
        let votes = self.tee_state.vote_measurement(action, &participant);

        // Removal requires ALL participants to vote
        let total_participants = threshold_parameters.participants().len() as u64;
        if votes >= total_participants {
            let removed = self.tee_state.remove_measurement(&measurement);
            log!("OS measurement remove result: {}", removed);
        }

        Ok(())
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L74-86)
```rust
pub struct TeeState {
    pub(crate) allowed_docker_image_hashes: AllowedDockerImageHashes,
    pub(crate) allowed_launcher_images: AllowedLauncherImages,
    pub(crate) votes: CodeHashesVotes,
    pub(crate) launcher_votes: LauncherHashVotes,
    /// Mapping of TLS public key of a participant to its [`NodeAttestation`].
    /// Attestations are stored for any valid participant that has submitted one, not
    /// just for the currently active participants. Callers must not assume this map is
    /// small; use the key-indexed accessors rather than scanning the whole collection.
    pub(crate) stored_attestations: IterableMap<Ed25519PublicKey, NodeAttestation>,
    pub(crate) allowed_measurements: AllowedMeasurements,
    pub(crate) measurement_votes: MeasurementVotes,
}
```

**File:** crates/contract/src/tee/tee_state.rs (L238-244)
```rust
    pub fn reverify_and_cleanup_participants(
        &mut self,
        participants: &Participants,
        tee_upgrade_deadline_duration: Duration,
    ) -> TeeValidationResult {
        self.allowed_docker_image_hashes
            .cleanup_expired_hashes(tee_upgrade_deadline_duration);
```

**File:** crates/contract/src/tee/tee_state.rs (L287-303)
```rust
    pub fn get_allowed_mpc_docker_image_hashes(
        &self,
        tee_upgrade_deadline_duration: Duration,
    ) -> Vec<NodeImageHash> {
        self.get_allowed_mpc_docker_images(tee_upgrade_deadline_duration)
            .into_iter()
            .map(|entry| entry.image_hash)
            .collect()
    }

    pub fn get_allowed_mpc_docker_images(
        &self,
        tee_upgrade_deadline_duration: Duration,
    ) -> Vec<AllowedMpcDockerImage> {
        self.allowed_docker_image_hashes
            .get(tee_upgrade_deadline_duration)
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L305-316)
```rust
    pub fn whitelist_tee_proposal(
        &mut self,
        tee_proposal: NodeImageHash,
        tee_upgrade_deadline_duration: Duration,
    ) {
        self.votes.clear_votes();
        // Add compose hashes for the new MPC image across all allowed launcher images
        self.allowed_launcher_images
            .add_mpc_image_compose_hashes(&tee_proposal);
        self.allowed_docker_image_hashes
            .insert(tee_proposal, tee_upgrade_deadline_duration);
    }
```

**File:** crates/near-mpc-contract-interface/src/method_names.rs (L23-27)
```rust
pub const VOTE_CODE_HASH: &str = "vote_code_hash";
pub const VOTE_ADD_LAUNCHER_HASH: &str = "vote_add_launcher_hash";
pub const VOTE_REMOVE_LAUNCHER_HASH: &str = "vote_remove_launcher_hash";
pub const VOTE_ADD_OS_MEASUREMENT: &str = "vote_add_os_measurement";
pub const VOTE_REMOVE_OS_MEASUREMENT: &str = "vote_remove_os_measurement";
```

**File:** docs/running-an-mpc-node-in-tdx-external-guide.md (L1824-1827)
```markdown
### Remove Old Launcher Manifest Digest / OS Measurements

After all operators have migrated to the new CVM, participants should vote to remove the old launcher manifest digest using `vote_remove_launcher_hash` and/or old OS measurements using `vote_remove_os_measurement`. This requires **all** participants to vote, ensuring no node is still running with the old configuration.

```
