### Title
No Revocation Mechanism for Whitelisted MPC Docker Image Hashes in `vote_code_hash` - (File: crates/contract/src/lib.rs)

### Summary

The MPC contract's `vote_code_hash` function permanently adds Docker image hashes to the TEE attestation whitelist with no corresponding `vote_remove_code_hash` function. Unlike launcher hashes (`vote_remove_launcher_hash`) and OS measurements (`vote_remove_os_measurement`), which both have explicit removal paths, MPC Docker image hashes can only be evicted by a passive 7-day time-based expiry triggered by a successor hash being voted in. If a compromised image hash is voted in — for example, via a supply-chain attack that deceives threshold participants — there is no emergency revocation path. Nodes running the compromised image retain valid attestation status and can participate in threshold signing for the full 7-day grace window.

### Finding Description

`vote_code_hash` in `crates/contract/src/lib.rs` calls `tee_state.whitelist_tee_proposal`, which inserts the hash into `AllowedDockerImageHashes` and clears pending votes. [1](#0-0) 

The `AllowedDockerImageHashes::insert` method in `crates/contract/src/tee/proposal.rs` only appends entries; there is no `remove` method on the struct. [2](#0-1) 

The only expiry mechanism is time-based: `valid_entries` computes a cutoff index based on which entry's grace-period deadline has passed, and always returns at least the newest entry as a disaster-recovery fallback. [3](#0-2) 

The `TeeState` struct exposes `remove_launcher_image` and `remove_measurement` but has no `remove_docker_image_hash` counterpart. [4](#0-3) 

The operator guide explicitly acknowledges the gap: *"There is no `vote_remove_code_hash`. Once a successor hash is voted in, the previous hash remains valid for a 7-day grace period and then auto-expires."* [5](#0-4) 

The contract API table confirms the asymmetry: `vote_remove_launcher_hash` (ALL participants) and `vote_remove_os_measurement` (ALL participants) exist, but no `vote_remove_code_hash` is listed. [6](#0-5) 

### Impact Explanation

The TEE attestation system is the gating mechanism that determines which nodes may participate in threshold signing. A node's attestation is accepted only if its Docker image hash appears in the whitelist at the time `submit_participant_info` is called and at each `re_verify` check. [7](#0-6) 

If a compromised image hash is whitelisted, nodes running that image can submit valid attestations and participate in signing for up to 7 days with no way for the honest majority to stop them short of that window. A backdoored image running on threshold-many nodes could exfiltrate key shares or co-sign unauthorized transactions. Even below threshold, a compromised node participating in presignature generation (triples, nonces) can bias or leak partial signing material. This breaks the production safety invariant that only verified, trusted code may touch key material — matching the **Medium** allowed impact: *participant-state manipulation that breaks production safety/accounting invariants*.

### Likelihood Explanation

MPC node images are updated approximately monthly. Each upgrade cycle requires threshold participants to vote for a new manifest digest. A supply-chain attack (compromised build pipeline, tampered DockerHub image, or a critical vulnerability discovered post-deployment) could result in a malicious hash being voted in by good-faith participants. The absence of an emergency removal path means the window of exposure is fixed at 7 days regardless of how quickly the compromise is detected. This is a realistic operational risk given the upgrade frequency and the explicit acknowledgment in the operator guide that no removal command exists.

### Recommendation

Add a `vote_remove_code_hash` function requiring **all** participants to vote (matching the bar set for `vote_remove_launcher_hash` and `vote_remove_os_measurement`). The implementation should:

1. Add a `remove` method to `AllowedDockerImageHashes` that refuses to remove the last entry (preserving the disaster-recovery invariant).
2. Add a `remove_docker_image_hash` method to `TeeState`.
3. Expose a `vote_remove_code_hash(code_hash: NodeImageHash)` public function on `MpcContract` gated by `voter_or_panic()` and requiring unanimous votes, mirroring `vote_remove_launcher_hash`.

The HOT TEE governance contract design already anticipates this — its method table includes `vote_remove_code_hash` — confirming the pattern is understood and intentional for that contract. [8](#0-7) 

### Proof of Concept

**Step 1 — Compromised hash voted in (supply-chain attack):**
```
# Threshold participants vote for a hash whose image contains a backdoor
near contract call-function as-transaction v1.signer \
  vote_code_hash json-args '{"code_hash": "<COMPROMISED_HASH>"}' \
  ... sign-as participant-N ...
# After threshold votes: hash is whitelisted, votes cleared
```

**Step 2 — Compromise discovered; no removal path:**
```
# Participants attempt to remove the hash — no such method exists
near contract call-function as-transaction v1.signer \
  vote_remove_code_hash ...   # ERROR: method does not exist
```

**Step 3 — Compromised nodes maintain valid attestation for 7 days:**
```
# Nodes running the backdoored image call submit_participant_info
# tee_state.add_participant() succeeds because the hash is still whitelisted
# reverify_participants() returns TeeQuoteStatus::Valid
# Compromised nodes participate in respond(), vote_pk(), vote_reshared(), etc.
```

**Step 4 — Only mitigation is voting in a successor hash:**
```
near contract call-function as-transaction v1.signer \
  vote_code_hash json-args '{"code_hash": "<CLEAN_HASH>"}' ...
# Old compromised hash now has a 7-day grace period — still valid until expiry
```

The `valid_entries` logic confirms both hashes coexist until the grace period of the successor elapses: [3](#0-2)  and the test `only_latest_hash_after_grace_period` demonstrates this explicitly. [9](#0-8)

### Citations

**File:** crates/contract/src/lib.rs (L1407-1430)
```rust
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

**File:** crates/contract/src/tee/proposal.rs (L204-231)
```rust
    pub fn insert(&mut self, code_hash: NodeImageHash, tee_upgrade_deadline_duration: Duration) {
        self.cleanup_expired_hashes(tee_upgrade_deadline_duration);

        // Remove the old entry if it exists
        if let Some(pos) = self
            .allowed_tee_proposals
            .iter()
            .position(|entry| entry.image_hash == code_hash)
        {
            self.allowed_tee_proposals.remove(pos);
        }

        let new_entry = AllowedMpcDockerImage {
            image_hash: code_hash,
            added: Timestamp::now(),
        };

        // Find the correct position to maintain sorted order by `added`
        let insert_index = self
            .allowed_tee_proposals
            .iter()
            // strictly less, `<`, such that new entries take higher precedence
            // if two entries have the exact same time stamp.
            .rposition(|entry| new_entry.added < entry.added)
            .unwrap_or(self.allowed_tee_proposals.len());

        self.allowed_tee_proposals.insert(insert_index, new_entry);
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L163-175)
```rust
        let accepted_measurements = self.get_accepted_measurements();
        // TODO(#3264): run DCAP in the verifier contract (Promise + callback) and
        // do the post-DCAP checks here, instead of verifying locally in-WASM.
        let AcceptedAttestation {
            attestation: verified_attestation,
            advisory_ids,
        } = attestation.verify_locally(
            expected_report_data.into(),
            Self::current_time_seconds(),
            &self.get_allowed_mpc_docker_image_hashes(tee_upgrade_deadline_duration),
            &self.get_allowed_launcher_compose_hashes(),
            &accepted_measurements,
        )?;
```

**File:** crates/contract/src/tee/tee_state.rs (L349-379)
```rust
    pub fn remove_launcher_image(&mut self, launcher_hash: &LauncherImageHash) -> bool {
        self.launcher_votes.clear_votes();
        self.allowed_launcher_images.remove(launcher_hash)
    }

    /// Returns all allowed launcher image hashes.
    pub fn get_allowed_launcher_hashes(&self) -> Vec<LauncherImageHash> {
        self.allowed_launcher_images.launcher_hashes()
    }

    /// Casts a vote for adding or removing an OS measurement.
    /// Returns the total number of votes for the same action.
    pub fn vote_measurement(
        &mut self,
        action: MeasurementVoteAction,
        participant: &AuthenticatedParticipantId,
    ) -> u64 {
        self.measurement_votes.vote(action, participant)
    }

    /// Adds a new measurement set to the allowed list. Clears measurement votes.
    pub fn add_measurement(&mut self, measurement: ContractExpectedMeasurements) -> bool {
        self.measurement_votes.clear_votes();
        self.allowed_measurements.add(measurement)
    }

    /// Removes a measurement set from the allowed list. Clears measurement votes.
    pub fn remove_measurement(&mut self, measurement: &ContractExpectedMeasurements) -> bool {
        self.measurement_votes.clear_votes();
        self.allowed_measurements.remove(measurement)
    }
```

**File:** docs/running-an-mpc-node-in-tdx-external-guide.md (L1619-1619)
```markdown
> **Note:** There is no `vote_remove_code_hash`. Once a successor hash is voted in, the previous hash remains valid for a 7-day grace period and then auto-expires — so unlike launcher and OS-measurement voting there is no explicit remove command.
```

**File:** crates/contract/README.md (L301-305)
```markdown
| `vote_code_hash(code_hash: CodeHash)`                                               | Votes to add new whitelisted TEE Docker image code hashes.                                                                                                                                                                              | `Result<(), Error>`       | TBD             | TBD                |
| `vote_add_launcher_hash(launcher_hash: LauncherImageHash)`                          | Votes to add a launcher image hash to the allowed set. Requires threshold votes.                                                                                                                                                        | `Result<(), Error>`       | TBD             | TBD                |
| `vote_remove_launcher_hash(launcher_hash: LauncherImageHash)`                       | Votes to remove a launcher image hash. Requires ALL participants to vote.                                                                                                                                                               | `Result<(), Error>`       | TBD             | TBD                |
| `vote_add_os_measurement(measurement: ContractExpectedMeasurements)`                | Votes to add an OS measurement set (MRTD, RTMR0-2, key-provider event digest). Requires threshold votes.                                                                                                                               | `Result<(), Error>`       | TBD             | TBD                |
| `vote_remove_os_measurement(measurement: ContractExpectedMeasurements)`             | Votes to remove an OS measurement set. Requires ALL participants to vote.                                                                                                                                                               | `Result<(), Error>`       | TBD             | TBD                |
```

**File:** docs/hot-tee-signing-design.md (L397-399)
```markdown
| `vote_code_hash(code_hash)` | Call | Governor | Vote for a new Docker image hash |
| `vote_remove_code_hash(code_hash)` | Call | Governor | Vote to remove a Docker image hash before natural expiry |
| `vote_add_launcher_hash(launcher_hash)` | Call | Governor | Vote for a new launcher image hash (threshold) |
```

**File:** crates/contract/tests/inprocess/attestation_submission.rs (L553-584)
```rust
fn only_latest_hash_after_grace_period() {
    const FIRST_ENTRY_TIME_NS: u64 = NANOS_IN_SECOND; // 1s
    const SECOND_ENTRY_TIME_NS: u64 = 4 * NANOS_IN_SECOND; // 1s
    const GRACE_PERIOD_NS: u64 = 10 * NANOS_IN_SECOND; // 10s

    let init_config = near_mpc_contract_interface::types::InitConfig {
        tee_upgrade_deadline_duration_seconds: Some(GRACE_PERIOD_NS / NANOS_IN_SECOND),
        ..Default::default()
    };

    let mut setup = TestSetupBuilder::new()
        .with_init_config(init_config)
        .build();

    let old_hash = [1; 32];
    let successor_hash = [2; 32];

    setup.vote_with_all_participants(old_hash, FIRST_ENTRY_TIME_NS);
    assert_allowed_docker_image_hashes!(&setup, FIRST_ENTRY_TIME_NS, &[old_hash]);
    setup.vote_with_all_participants(successor_hash, SECOND_ENTRY_TIME_NS);
    assert_allowed_docker_image_hashes!(&setup, SECOND_ENTRY_TIME_NS, &[old_hash, successor_hash]);

    assert_allowed_docker_image_hashes!(
        &setup,
        SECOND_ENTRY_TIME_NS + GRACE_PERIOD_NS,
        &[old_hash, successor_hash]
    );
    assert_allowed_docker_image_hashes!(
        &setup,
        SECOND_ENTRY_TIME_NS + GRACE_PERIOD_NS + 1,
        &[successor_hash]
    );
```
