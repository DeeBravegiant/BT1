Now I have enough context to analyze the vulnerability. Let me trace the exact logic carefully.

### Title
Off-by-one in `AllowedDockerImageHashes::valid_entries` allows expired image hash to remain accepted after a newer hash is whitelisted — (`crates/contract/src/tee/proposal.rs`)

---

### Summary

`valid_entries` uses `rposition` to find the rightmost expired entry and then returns `get(cutoff_index..)`, which **includes** the expired entry itself. When exactly one entry is expired and a newer entry is not yet expired, the expired hash is returned as part of the valid set. Every downstream caller — `add_participant`, `reverify_participants`, and `cleanup_expired_hashes` — therefore treats the expired hash as still allowed.

---

### Finding Description

`valid_entries` is the single source of truth for which image hashes are currently accepted: [1](#0-0) 

`rposition` scans right-to-left and returns the index of the **last** entry satisfying `grace_period_deadline < current_time` (i.e., the last expired entry). The slice `get(cutoff_index..)` then starts **at** that index, so the expired entry is included in the returned vector.

Concrete state trace with two entries:

| Index | Hash | Status at time `T1 + grace + 1` |
|-------|------|----------------------------------|
| 0 | H1 (added T1) | expired |
| 1 | H2 (added T2, T2 > T1) | **not** expired |

- `rposition` scans right-to-left: H2 → false, H1 → **true** → returns `Some(0)`
- `get(0..)` → `[H1, H2]` — H1 is included despite being expired

The "keep at least one" invariant (all-expired case) works correctly because then `rposition` returns the index of the last entry and `get(last..)` yields exactly one element. The bug only fires when there is at least one expired entry **and** at least one non-expired entry.

`cleanup_expired_hashes` calls `valid_entries` and replaces the internal list with its output: [2](#0-1) 

So H1 is never removed from `allowed_tee_proposals` as long as H2 is still within its grace period. Both `add_participant` and `reverify_participants` call `get_allowed_mpc_docker_image_hashes`, which delegates to `valid_entries`: [3](#0-2) [4](#0-3) 

`reverify_and_cleanup_participants` (called by `verify_tee()`) also calls `cleanup_expired_hashes` first, which — due to the same bug — leaves H1 in the list, so re-verification also passes for H1-bound attestations: [5](#0-4) 

---

### Impact Explanation

A node operator running the old image H1 can call `submit_participant_info` with a genuine TDX quote bound to H1 after governance has voted in H2 and H1's grace period has elapsed. The contract accepts the attestation, stores the node in `stored_attestations` as a valid participant, and the node continues to pass periodic `verify_tee()` re-checks. The node participates in threshold signing with an image that governance explicitly intended to retire. If H1 carries a vulnerability (the typical reason for a forced upgrade), the attacker retains signing capability with the vulnerable image indefinitely — until H2 also expires and both entries are in the all-expired case, at which point H1 is finally evicted.

This is an **attestation authorization bypass**: the contract's image-hash expiry enforcement is silently defeated for the entire window `[T1 + grace_period, T2 + grace_period]`.

---

### Likelihood Explanation

The window is as wide as the grace period itself (configured via `tee_upgrade_deadline_duration_seconds`). Any node operator who declines to upgrade simply keeps submitting attestations bound to H1 during this window. No threshold collusion is required — the T-of-N vote that whitelisted H2 is normal governance, not attacker-controlled. The attacker is a single participant who refuses to upgrade.

---

### Recommendation

Change `get(cutoff_index..)` to `get(cutoff_index + 1..)`, and preserve the "always keep at least one" invariant separately:

```rust
fn valid_entries(&self, tee_upgrade_deadline_duration: Duration) -> Vec<AllowedMpcDockerImage> {
    let current_time = Timestamp::now();
    let cutoff_index = self
        .allowed_tee_proposals
        .iter()
        .rposition(|entry| {
            entry.added
                .checked_add(tee_upgrade_deadline_duration)
                .map_or(true, |deadline| deadline < current_time)
        });

    let start = match cutoff_index {
        // At least one entry is expired; start after it.
        // If that would leave nothing, fall back to the last entry.
        Some(i) => {
            let next = i + 1;
            if next < self.allowed_tee_proposals.len() { next }
            else { i }   // all entries expired: keep the last one
        }
        // Nothing expired yet: return everything.
        None => 0,
    };

    self.allowed_tee_proposals.get(start..).unwrap_or(&[]).to_vec()
}
```

---

### Proof of Concept

```rust
#[test]
fn expired_hash_must_not_appear_in_valid_entries() {
    use std::time::Duration;
    use near_sdk::{test_utils::VMContextBuilder, testing_env};

    const GRACE: Duration = Duration::from_secs(10 * 24 * 60 * 60); // 10 days
    const NS: u64 = 1_000_000_000;

    let mut allowed = AllowedDockerImageHashes::default();

    // T1 = 1 s: insert H1
    testing_env!(VMContextBuilder::new().block_timestamp(1 * NS).build());
    allowed.insert(dummy_code_hash(1), GRACE);

    // T2 = T1 + 1 s: insert H2 (well within H1's grace period)
    testing_env!(VMContextBuilder::new().block_timestamp(2 * NS).build());
    allowed.insert(dummy_code_hash(2), GRACE);

    // Advance to T1 + grace + 1 ns: H1 is expired, H2 is NOT expired
    let t_expired = 1 * NS + GRACE.as_nanos() as u64 + 1;
    testing_env!(VMContextBuilder::new().block_timestamp(t_expired).build());

    let hashes = allowed.get_image_hashes(GRACE);

    // BUG: currently returns [H1, H2]; should return only [H2]
    assert!(
        !hashes.contains(&dummy_code_hash(1)),
        "expired H1 must not appear in valid_entries, but got: {:?}", hashes
    );
    assert!(hashes.contains(&dummy_code_hash(2)));
}
```

This test fails against the current implementation, confirming that H1 is returned by `valid_entries` after its grace period has elapsed, and therefore `add_participant` would accept an attestation bound to H1.

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

**File:** crates/contract/src/tee/tee_state.rs (L169-175)
```rust
        } = attestation.verify_locally(
            expected_report_data.into(),
            Self::current_time_seconds(),
            &self.get_allowed_mpc_docker_image_hashes(tee_upgrade_deadline_duration),
            &self.get_allowed_launcher_compose_hashes(),
            &accepted_measurements,
        )?;
```

**File:** crates/contract/src/tee/tee_state.rs (L211-228)
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
```

**File:** crates/contract/src/tee/tee_state.rs (L243-244)
```rust
        self.allowed_docker_image_hashes
            .cleanup_expired_hashes(tee_upgrade_deadline_duration);
```
