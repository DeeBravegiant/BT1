### Title
Single Participant Below Signing Threshold Can Permanently Block TEE Image Rotation — (`crates/contract/src/lib.rs`)

---

### Summary

`vote_remove_launcher_hash` and `vote_remove_os_measurement` are hardcoded to require **all** `n` participants to vote before removal executes. A single Byzantine participant strictly below the signing threshold can permanently prevent old TEE launcher or OS measurement hashes from being removed, freezing the TEE upgrade cycle and leaving potentially vulnerable images permanently in the allowed set.

---

### Finding Description

Both removal functions count votes against the full participant count (`total_participants`), not the governance threshold:

```rust
// vote_remove_launcher_hash (lib.rs ~L1487-1492)
let total_participants = threshold_parameters.participants().len() as u64;
if votes >= total_participants {
    let removed = self.tee_state.remove_launcher_image(&launcher_hash);
```

```rust
// vote_remove_os_measurement (lib.rs ~L1544-1548)
let total_participants = threshold_parameters.participants().len() as u64;
if votes >= total_participants {
    let removed = self.tee_state.remove_measurement(&measurement);
```

This is a hardcoded n-of-n unanimity requirement, independent of the configured governance threshold (which is bounded between 60% and 100% of `n`). The codebase itself acknowledges the risk of the 100% upper bound in `thresholds.rs`:

```rust
/// Upper bound on the GovernanceThreshold for `n` participants:
/// Currently set to 100% of participants but would be a discussion subject
/// to drop this upper bound down not to have problems with smart contract
/// being locked if t = n and if an operator stops voting
pub(crate) fn governance_threshold_upper_relative_bound(n: u64) -> u64 {
    n
}
```

The removal functions go further than the configurable governance threshold — they unconditionally require every single participant, making them permanently blockable by one participant.

---

### Impact Explanation

The TEE upgrade lifecycle requires:
1. Vote to add new launcher/OS hash (succeeds at governance threshold `t`)
2. All nodes migrate to new image
3. **Vote to remove old launcher/OS hash** — requires n-of-n

If step 3 never completes, the old (potentially vulnerable) launcher hash or OS measurement remains permanently in `allowed_launcher_images` / `allowed_os_measurements`. The contract continues to accept attestations from nodes running the old image. If the old image contains a security vulnerability, those nodes remain valid participants in threshold signing indefinitely, undermining the entire TEE security model.

This breaks the production safety invariant that the network can rotate out compromised TEE images.

---

### Likelihood Explanation

A single participant strictly below the signing threshold (which is ≥60%) can trigger this by:
- Going permanently offline (hardware failure, operator exit, key loss)
- Deliberately refusing to call `vote_remove_launcher_hash` / `vote_remove_os_measurement`

No privileged access, no collusion, no leaked keys are required. The attacker need only be a registered participant and withhold one vote. In a production network with multiple operators, permanent unavailability of a single node is a realistic scenario.

---

### Recommendation

Replace the hardcoded `total_participants` unanimity check with the governance threshold `self.threshold()?.value()`, consistent with how `vote_add_launcher_hash` and `vote_add_os_measurement` already operate:

```rust
// Consistent with vote_add_launcher_hash:
if votes >= self.threshold()?.value() {
    let removed = self.tee_state.remove_launcher_image(&launcher_hash);
```

Alternatively, use a supermajority (e.g., 80%) rather than unanimity, so that removal can proceed even if one participant is offline, while still requiring broad consensus.

---

### Proof of Concept

1. Network has `n = 5` participants, governance threshold `t = 3` (60%).
2. Participants vote to add a new launcher hash — succeeds after 3 votes.
3. All 5 nodes migrate to the new launcher image.
4. Participants call `vote_remove_launcher_hash(old_hash)` to decommission the old image.
5. Participants 1–4 vote. `votes = 4`. Check: `4 >= 5` → **false**. Old hash not removed.
6. Participant 5 is offline (or malicious and refuses to vote).
7. Old launcher hash remains in `allowed_launcher_images` permanently.
8. Nodes running the old (potentially vulnerable) launcher continue to pass `submit_participant_info` attestation checks and remain valid signers.
9. The TEE upgrade cycle is permanently frozen by one participant below the signing threshold.

**Relevant code:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** crates/contract/src/primitives/thresholds.rs (L19-25)
```rust
/// Upper bound on the GovernanceThreshold for `n` participants:
/// Currently set to 100% of participants but would be a discussion subject
/// to drop this upper bound down not to have problems with smart contract
/// being locked if t = n and if an operator stops voting
pub(crate) fn governance_threshold_upper_relative_bound(n: u64) -> u64 {
    n
}
```
