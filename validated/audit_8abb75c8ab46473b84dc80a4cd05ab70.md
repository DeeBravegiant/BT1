### Title
`get_sortable_validator_online_ratio` Ignores `endorsement_cutoff_threshold`, Causing Incorrect Validator Exemption from Kickout — (`File: chain/epoch-manager/src/validator_stats.rs`)

---

### Summary

The `endorsement_cutoff_threshold` parameter is applied when computing validator online ratios for **reward calculation**, but is silently dropped (hardcoded to `None`) when computing online ratios for **kickout-exemption sorting**. This inconsistency causes the wrong validators to be exempted from kickout at epoch boundaries, corrupting the next epoch's validator set.

---

### Finding Description

In `finalize_epoch`, the epoch manager constructs `ValidatorOnlineThresholds` with `endorsement_cutoff_threshold: Some(epoch_config.chunk_validator_only_kickout_threshold)` and passes it to `calculate_reward`: [1](#0-0) 

Inside `calculate_reward`, the cutoff is forwarded to `get_validator_online_ratio`: [2](#0-1) 

The cutoff binarizes the endorsement component: if a validator's endorsement ratio is below the threshold it is treated as `0`; if at or above, it is treated as `1`: [3](#0-2) 

However, the **sorting** used to decide which validators are exempted from kickout calls `get_sortable_validator_online_ratio`, which **always passes `None`** for the cutoff: [4](#0-3) 

This sorted list is then fed directly into `compute_exempted_kickout`: [5](#0-4) 

The exemption logic iterates from highest to lowest online ratio and exempts validators until enough stake is retained. Because the sorting uses the raw endorsement ratio instead of the binarized one, the ordering can be wrong.

---

### Impact Explanation

**Concrete corrupted protocol value:** the validator set for epoch `T+2`, computed during `finalize_epoch` at the end of epoch `T`.

**Scenario:**

| Validator | Role | Endorsement ratio | Cutoff = 80% |
|---|---|---|---|
| A | chunk-validator-only | 79% | treated as **0.0** (below cutoff) |
| B | block+chunk producer | block 70%, chunk 70% | raw average **0.70** |

- **Correct sorting** (cutoff applied): A → 0.0, B → 0.70. B is exempted first; A is kicked out for low endorsements.
- **Actual sorting** (no cutoff): A → 0.79, B → 0.70. A is exempted first; B is kicked out despite having a higher "true" online ratio.

Result: a chunk-validator-only validator with endorsement below the kickout threshold escapes kickout, while a better-performing block+chunk producer is incorrectly removed from the next epoch's validator set. All nodes compute the same wrong result (no consensus split), but the validator set diverges from protocol intent.

---

### Likelihood Explanation

The conditions are reachable by any unprivileged validator:

1. A chunk-validator-only validator deliberately maintains endorsement ratio just below `chunk_validator_only_kickout_threshold` (e.g., 79% when threshold is 80%).
2. At least one block+chunk producer has a raw average online ratio lower than the chunk-validator-only's raw endorsement ratio.
3. The `validator_max_kickout_stake_perc` cap is active (i.e., not 100%), so the exemption logic runs.

`chunk_validator_only_kickout_threshold` is set to 80 in production epoch configs: [6](#0-5) 

The exemption cap (`validator_max_kickout_stake_perc`) is also a live production parameter. The scenario is realistic whenever the network has both chunk-validator-only seats and block+chunk producers with varying uptime.

---

### Recommendation

Pass the `endorsement_cutoff_threshold` through to `get_sortable_validator_online_ratio` so that the sorting used for exemption is consistent with the binarization applied during reward calculation. Concretely, `get_sortable_validator_online_ratio` should accept an `Option<u8>` cutoff parameter and forward it to `get_validator_online_ratio`, and `compute_validators_to_reward_and_kickout` should pass `Some(chunk_validator_only_kickout_threshold)` when calling it.

---

### Proof of Concept

**Root cause — sorting ignores cutoff:**

```rust
// validator_stats.rs line 110-111
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);  // cutoff always None
``` [7](#0-6) 

**Reward path — cutoff correctly applied:**

```rust
// reward_calculator.rs line 95-96
let production_ratio =
    get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
``` [2](#0-1) 

**Cutoff set in production finalize_epoch:**

```rust
endorsement_cutoff_threshold: Some(
    epoch_config.chunk_validator_only_kickout_threshold,
),
``` [8](#0-7) 

**Exemption sorting calls the cutoff-blind function:**

```rust
.map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
``` [9](#0-8)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L500-516)
```rust
        let mut sorted_validators = validator_block_chunk_stats
            .iter()
            .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
            .collect_vec();
        sorted_validators.sort_by(validator_comparator);
        let accounts_sorted_by_online_ratio =
            sorted_validators.into_iter().map(|(_, account)| account.clone()).collect_vec();

        let exempt_perc =
            100_u8.checked_sub(config.validator_max_kickout_stake_perc).unwrap_or_default();
        let exempted_validators = Self::compute_exempted_kickout(
            epoch_info,
            &accounts_sorted_by_online_ratio,
            total_stake,
            exempt_perc,
            prev_validator_kickout,
        );
```

**File:** chain/epoch-manager/src/lib.rs (L898-913)
```rust
            let online_thresholds = ValidatorOnlineThresholds {
                online_min_threshold: epoch_config.online_min_threshold,
                online_max_threshold: epoch_config.online_max_threshold,
                endorsement_cutoff_threshold: Some(
                    epoch_config.chunk_validator_only_kickout_threshold,
                ),
            };
            self.reward_calculator.calculate_reward(
                validator_block_chunk_stats,
                &validator_stake,
                *block_info.total_supply(),
                epoch_protocol_version,
                epoch_duration,
                online_thresholds,
                epoch_config.max_inflation_rate,
            )
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L94-96)
```rust
        for (account_id, stats) in validator_block_chunk_stats {
            let production_ratio =
                get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
```

**File:** chain/epoch-manager/src/validator_stats.rs (L110-117)
```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);
    let mut bytes: [u8; size_of::<U256>()] = [0; size_of::<U256>()];
    ratio.numer().to_little_endian(&mut bytes);
    let bignumer = BigUint::from_bytes_le(&bytes);
    ratio.denom().to_little_endian(&mut bytes);
    let bigdenom = BigUint::from_bytes_le(&bytes);
    BigRational::new(bignumer.try_into().unwrap(), bigdenom.try_into().unwrap())
```

**File:** chain/epoch-manager/src/validator_stats.rs (L124-134)
```rust
fn get_endorsement_ratio(stats: &ValidatorStats, cutoff_threshold: Option<u8>) -> (u64, u64) {
    let (numer, denom) = if stats.expected == 0 {
        debug_assert_eq!(stats.produced, 0);
        (0, 0)
    } else if let Some(threshold) = cutoff_threshold {
        if stats.less_than(threshold) { (0, 1) } else { (1, 1) }
    } else {
        (stats.produced, stats.expected)
    };
    (numer, denom)
}
```

**File:** core/primitives/res/epoch_configs/testnet/29.json (L12-12)
```json
  "chunk_validator_only_kickout_threshold": 80,
```
