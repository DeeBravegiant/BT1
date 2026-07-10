### Title
Off-by-one in `AllowedDockerImageHashes::valid_entries` includes the most-recently-expired TEE image hash in the valid set — (`crates/contract/src/tee/proposal.rs`)

---

### Summary

`valid_entries` uses `rposition` to find the rightmost expired entry, then slices from that index inclusive. This means the expired entry at the cutoff is always returned as valid, allowing a node attesting with an expired image hash to pass `add_participant` and `reverify_participants`.

---

### Finding Description

`valid_entries` is the single source of truth for which TEE image hashes are currently accepted: [1](#0-0) 

The logic:

1. `rposition` scans right-to-left and returns the index of the **last** entry satisfying `grace_period_deadline < current_time` — i.e., the last **expired** entry.
2. `get(cutoff_index..)` slices from that index **inclusive**, so the expired entry at `cutoff_index` is included in the returned slice.

Concrete trace with `allowed_tee_proposals = [H1 (added t=0), H2 (added t=1)]` and current time between H1's deadline and H2's deadline:

- `rposition` checks H2 (index 1): not expired → `false`
- `rposition` checks H1 (index 0): expired → `true`; returns `Some(0)`
- `cutoff_index = 0`
- `get(0..)` → `[H1, H2]` — H1 is expired but included

The correct slice should start at `cutoff_index + 1`.

The `unwrap_or(0)` fallback is only reached when **no** entry is expired (all valid), in which case `get(0..)` returning everything is correct. The bug is exclusive to the mixed state (some expired, some not).

`cleanup_expired_hashes` delegates entirely to `valid_entries` and replaces the internal list with its output, so the cleanup also fails to remove H1: [2](#0-1) 

The existing `test_clean_expired` does **not** catch this because it sets the clock past **both** entries' deadlines, so `rposition` finds H2 at index 1 and `get(1..)` returns only H2 — the off-by-one is masked when all entries are expired: [3](#0-2) 

---

### Impact Explanation

`get_allowed_mpc_docker_image_hashes` calls `valid_entries` directly: [4](#0-3) 

This list is passed to `attestation.verify_locally` inside `add_participant`: [5](#0-4) 

And to `re_verify` inside `reverify_participants`: [6](#0-5) 

Because H1 is present in the allowed list, a node attesting with H1 passes both `add_participant` and `reverify_participants`. It is stored in `stored_attestations` and survives `reverify_and_cleanup_participants`, remaining an authorized signer. If H1 was rotated out because it contains a known exploitable vulnerability (the normal reason for rotation), this node can participate in threshold signing with a compromised enclave, enabling potential key-share extraction and unauthorized signature issuance.

---

### Likelihood Explanation

No timestamp manipulation is required. The vulnerable state — H1 expired, H2 not yet expired — is the **normal operational state** during every image-hash rotation. It persists for the entire duration of H2's grace period (configured as 10 days in tests). Any node operator who has not upgraded from H1 to H2 within H1's grace period can exploit this window. The attacker does not need to be unprivileged in the traditional sense; they need only be a registered participant running the old image.

---

### Recommendation

Change `valid_entries` to advance past the expired cutoff entry:

```rust
let cutoff_index = self
    .allowed_tee_proposals
    .iter()
    .rposition(|allowed_docker_image| {
        // ... same predicate ...
        grace_period_deadline < current_time
    })
    .map(|i| i + 1)   // skip the expired entry itself
    .unwrap_or(0);
```

Add a unit test with exactly two entries where H1 is expired and H2 is valid, asserting H1 is absent from the result.

---

### Proof of Concept

```rust
#[test]
fn test_valid_entries_excludes_expired_entry() {
    let mut allowed = AllowedDockerImageHashes::default();
    let grace = Duration::from_secs(10);

    // H1 added at t=1s
    testing_env!(VMContextBuilder::new().block_timestamp(1_000_000_000).build());
    allowed.insert(dummy_code_hash(1), grace);

    // H2 added at t=2s
    testing_env!(VMContextBuilder::new().block_timestamp(2_000_000_000).build());
    allowed.insert(dummy_code_hash(2), grace);

    // Advance to t=12s: H1 deadline=11s (expired), H2 deadline=12s (not yet expired)
    testing_env!(VMContextBuilder::new().block_timestamp(12_000_000_000).build());

    let hashes = allowed.get_image_hashes(grace);
    // BUG: hashes contains dummy_code_hash(1) — the expired entry
    assert!(!hashes.contains(&dummy_code_hash(1)), "expired H1 must not be valid");
    assert!(hashes.contains(&dummy_code_hash(2)), "valid H2 must be present");
}
```

This test fails on the current code because `get(0..)` includes H1.

### Citations

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

**File:** crates/contract/src/tee/proposal.rs (L441-489)
```rust
    fn test_clean_expired() {
        let mut allowed = AllowedDockerImageHashes::default();
        let first_entry_time_nano_seconds = NANOS_IN_SECOND;

        testing_env!(
            VMContextBuilder::new()
                .block_timestamp(first_entry_time_nano_seconds)
                .build()
        );

        // Insert two proposals at different time intervals
        allowed.insert(dummy_code_hash(1), TEST_TEE_UPGRADE_DEADLINE_DURATION);

        let second_entry_time_nano_seconds = first_entry_time_nano_seconds + NANOS_IN_SECOND;
        testing_env!(
            VMContextBuilder::new()
                .block_timestamp(second_entry_time_nano_seconds)
                .build()
        );

        allowed.insert(dummy_code_hash(2), TEST_TEE_UPGRADE_DEADLINE_DURATION);

        let first_entry_expiry_time_nanoseconds = second_entry_time_nano_seconds
            + TEST_TEE_UPGRADE_DEADLINE_DURATION.as_nanos() as u64
            + 1;

        testing_env!(
            VMContextBuilder::new()
                .block_timestamp(first_entry_expiry_time_nanoseconds)
                .build()
        );

        allowed.cleanup_expired_hashes(TEST_TEE_UPGRADE_DEADLINE_DURATION);
        let proposals: Vec<_> = allowed.get(TEST_TEE_UPGRADE_DEADLINE_DURATION);

        // Only the second proposal should remain if the first is expired
        assert_eq!(proposals.len(), 1);
        assert_eq!(proposals[0].image_hash, dummy_code_hash(2));

        // Move block time far enough to expire both proposals. We always keep at least one
        // proposal in storage
        testing_env!(VMContextBuilder::new().block_timestamp(u64::MAX).build());

        allowed.cleanup_expired_hashes(TEST_TEE_UPGRADE_DEADLINE_DURATION);

        let proposals: Vec<_> = allowed.get(TEST_TEE_UPGRADE_DEADLINE_DURATION);

        assert_eq!(proposals.len(), 1);
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L166-175)
```rust
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

**File:** crates/contract/src/tee/tee_state.rs (L211-231)
```rust
        let allowed_mpc_docker_image_hashes =
            self.get_allowed_mpc_docker_image_hashes(tee_upgrade_deadline_duration);
        let allowed_launcher_compose_hashes = self.get_allowed_launcher_compose_hashes();
        let allowed_measurements = self.get_accepted_measurements();

        let participant_attestation = self.stored_attestations.get(&node_id.tls_public_key);
        let Some(participant_attestation) = participant_attestation else {
            return TeeQuoteStatus::Invalid("participant has no attestation".to_string());
        };

        // Verify the attestation quote
        let time_stamp_seconds = Self::current_time_seconds();
        match participant_attestation.verified_attestation.re_verify(
            time_stamp_seconds,
            &allowed_mpc_docker_image_hashes,
            &allowed_launcher_compose_hashes,
            &allowed_measurements,
        ) {
            Ok(()) => TeeQuoteStatus::Valid,
            Err(err) => TeeQuoteStatus::Invalid(err.to_string()),
        }
```

**File:** crates/contract/src/tee/tee_state.rs (L287-295)
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
```
