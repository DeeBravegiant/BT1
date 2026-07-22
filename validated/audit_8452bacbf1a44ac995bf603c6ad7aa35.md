### Title
Unchecked `fee_proposal_fri` During Startup Window Corrupts L2 Gas Price Floor — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

During the first `fee_proposal_window_size` (10) blocks, `is_proposal_init_valid` explicitly skips all bounds checking on `ProposalInit.fee_proposal_fri`. A malicious proposer can inject an arbitrarily large value, seeding the sliding-window median (`fee_actual`) with an extreme price. Once the window fills, `fee_actual` becomes the permanent floor for the L2 gas price, making all transactions unaffordable.

---

### Finding Description

`is_proposal_init_valid` in `validate_proposal.rs` validates `fee_proposal_fri` against `fee_actual` only when `fee_actual` is `Some`: [1](#0-0) 

`fee_actual` is `None` for the first `fee_proposal_window_size` (10) blocks because `compute_fee_actual` returns `None` when `height < window_size`: [2](#0-1) 

During this startup window, any value of `fee_proposal_fri` is accepted without bounds checking. The code comment explicitly acknowledges this: *"During initiation (fee_actual is None, <window_size blocks), bounds are not enforced."*

These values are recorded into `fee_proposals_window` unconditionally in `finalize_decision`: [3](#0-2) 

via: [4](#0-3) 

Once the window fills (height ≥ 10), `compute_fee_actual` computes the median of these values. If the attacker controls ≥6 of the 10 startup proposer slots, the median becomes the attacker's injected value (e.g., `u128::MAX`).

This `fee_actual` is then used in `calculate_next_l2_gas_price_for_fin` as the **floor** for the L2 gas price: [5](#0-4) 

And in `calculate_next_base_gas_price`, the final price is `max(adjusted_price, min_gas_price.0)`: [6](#0-5) 

If `effective_min = u128::MAX`, the next L2 gas price is permanently `u128::MAX`. The EIP-1559 downward adjustment is neutralized because the floor clamp overrides it on every block.

The `fee_proposal_margin_ppt = 2` (0.2%) guard that would normally bound subsequent proposals: [7](#0-6) 

only applies when `fee_actual` is `Some`, which is not the case during the startup window. There is no absolute upper bound on `fee_proposal_fri` at any point.

An honest proposer freezes at `l2_gas_price` when `fee_actual` is `None`: [8](#0-7) 

But a malicious proposer is not constrained to do so.

---

### Impact Explanation

**Impact: Critical — Incorrect fee/gas accounting with economic impact.**

Once `fee_actual` is set to `u128::MAX` at block 10, `calculate_next_l2_gas_price_for_fin` sets `effective_min = u128::MAX`, and `calculate_next_base_gas_price` returns `u128::MAX` as the next L2 gas price. All transactions with `max_price_per_unit < u128::MAX` fail fee validation at the gateway and batcher. The network becomes effectively unusable.

Recovery requires the `fee_actual` window to roll over with honest `fee_proposal_fri` values. Since subsequent proposals are bounded by ±0.2% of `fee_actual` per block (`fee_proposal_margin_ppt = 2`), recovering from `u128::MAX ≈ 3.4 × 10^38` to a normal price of ~1 gwei requires approximately:

```
log(3.4e38 / 1e9) / log(1 / 0.998) ≈ 34,000 blocks (~24 hours at 2.6 s/block)
```

The injected `fee_proposal_fri` is also bound into the `ProposalCommitment` via `proposal_commitment_from(partial_block_hash, fee_proposal)`: [9](#0-8) 

making the committed block hash reflect the injected value, so the attack is permanent without a chain revert.

---

### Likelihood Explanation

**Likelihood: Low-Medium.**

The attack requires controlling ≥6 of the first 10 proposer slots (to ensure the median of the window is the injected value). In a small or newly-bootstrapped validator set, a malicious coalition controlling a simple majority of validators can achieve this. The startup window occurs at genesis and after any restart or revert that clears the `fee_proposals_window`. The `initialize_fee_proposals_window` function re-populates the window from state_sync on restart: [10](#0-9) 

so restarts after block 10 are not vulnerable. The attack surface is narrowly at genesis or after a revert to below block 10.

---

### Recommendation

**Short term:** Add an absolute upper bound check on `fee_proposal_fri` that applies regardless of whether `fee_actual` is available. For example, enforce `fee_proposal_fri <= max_l2_gas_price` (a configurable parameter, analogous to `max_l1_gas_price_wei` already present in `ContextDynamicConfig`): [11](#0-10) 

**Long term:** During the startup window, use `l2_gas_price` (the current local price) as the reference for bounds checking instead of skipping the check entirely. This mirrors the honest proposer's behavior (`compute_proposer_fee_proposal` already freezes at `l2_gas_price` when `fee_actual` is `None`) and closes the unchecked window.

---

### Proof of Concept

1. At genesis (blocks 0–9), `fee_actual = None` because `height < window_size (10)`.
2. A malicious proposer (or coalition controlling ≥6 of the first 10 proposer slots) sets `fee_proposal_fri = GasPrice(u128::MAX)` in `ProposalInit`.
3. `is_proposal_init_valid` reaches the `if let (Some(fee_actual), Some(fee_proposal)) = ...` guard; since `fee_actual` is `None`, the guard is not entered and the value is accepted unconditionally.
4. `finalize_decision` calls `record_fee_proposal(height, Some(GasPrice(u128::MAX)))`, inserting the extreme value into `fee_proposals_window`.
5. At block 10, `compute_fee_actual` returns `Some(GasPrice(u128::MAX))` (median of the injected values).
6. `calculate_next_l2_gas_price_for_fin` computes `effective_min = max(config_min, u128::MAX) = u128::MAX`.
7. `calculate_next_base_gas_price` returns `GasPrice(u128::MAX)` as the next L2 gas price.
8. All subsequent transactions with `max_price_per_unit < u128::MAX` fail fee validation at the gateway (`validate_tx_l2_gas_price_within_threshold`) and batcher.
9. The network is effectively unusable for ~34,000 blocks (~24 hours) until the `fee_actual` window naturally decays.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L396-416)
```rust
    // Validate fee_proposal is within the configured margin of fee_actual.
    // During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
    if let (Some(fee_actual), Some(fee_proposal)) =
        (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
    {
        let (lower_bound, upper_bound) = fee_proposal_bounds(
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
        if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "Fee proposal out of bounds: fee_actual={}, fee_proposal={}, allowed \
                     range=[{lower_bound}, {upper_bound}]",
                    fee_actual.0, fee_proposal.0
                ),
            ));
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L56-92)
```rust
pub fn compute_fee_actual(
    fee_proposals_window: &BTreeMap<BlockNumber, Option<GasPrice>>,
    height: BlockNumber,
    window_size: u64,
) -> Option<GasPrice> {
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!(
            "Cannot compute fee_actual for height {height}: height is below window_size \
             ({window_size})"
        );
        return None;
    };
    let window_size_usize = usize::try_from(window_size).expect("window_size fits in usize");
    let mut window = Vec::with_capacity(window_size_usize);
    for source_height in (start..height.0).map(BlockNumber) {
        match fee_proposals_window.get(&source_height) {
            Some(Some(price)) => window.push(*price),
            Some(None) | None => {
                warn!(
                    "Cannot compute fee_actual for height {height}: fee_proposals_window has no \
                     recorded fee_proposal for height {source_height}"
                );
                return None;
            }
        }
    }
    window.sort();
    let mid = window_size_usize / 2;
    let median = if window_size_usize.is_multiple_of(2) {
        // Even: average of the two middle values, rounded down.
        // Overflow-safe averaging: a + (b - a) / 2 (safe because sorted, so b >= a).
        GasPrice(window[mid - 1].0 + (window[mid].0 - window[mid - 1].0) / 2)
    } else {
        window[mid]
    };
    Some(median)
}
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L163-171)
```rust
pub(crate) fn proposal_commitment_from(
    partial: PartialBlockHash,
    fee_proposal: Option<GasPrice>,
) -> ProposalCommitment {
    let Some(fee_proposal) = fee_proposal else {
        return ProposalCommitment(partial.0);
    };
    ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L308-310)
```rust
    fn record_fee_proposal(&mut self, height: BlockNumber, fee_proposal_fri: Option<GasPrice>) {
        self.fee_proposals_window.insert(height, fee_proposal_fri);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L326-353)
```rust
    pub async fn initialize_fee_proposals_window(
        &mut self,
        start_height: BlockNumber,
    ) -> StateSyncClientResult<()> {
        const STATE_SYNC_RETRY_INTERVAL: Duration = Duration::from_millis(500);
        let window_size = VersionedConstants::latest_constants().fee_proposal_window_size;
        let window_end_height = start_height.0;
        let window_start_height = window_end_height.saturating_sub(window_size);
        let mut pending_heights: VecDeque<BlockNumber> =
            (window_start_height..window_end_height).map(BlockNumber).collect();
        while let Some(block_number) = pending_heights.pop_front() {
            match self.deps.state_sync_client.get_block(block_number).await {
                Ok(block) => self.record_fee_proposal(
                    block_number,
                    block.block_header_without_hash.fee_proposal_fri,
                ),
                Err(StateSyncClientError::StateSyncError(StateSyncError::BlockNotFound(_))) => {
                    warn!(
                        "State sync not ready for height {block_number}; re-queueing after \
                         {STATE_SYNC_RETRY_INTERVAL:?}"
                    );
                    pending_heights.push_back(block_number);
                    tokio::time::sleep(STATE_SYNC_RETRY_INTERVAL).await;
                }
                Err(e) => return Err(e),
            }
        }
        Ok(())
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L478-482)
```rust
        let Some(fee_actual) = fee_actual else {
            warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
            SNIP35_FEE_PROPOSAL_FRI.set_lossy(self.l2_gas_price.0);
            return self.l2_gas_price;
        };
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L517-518)
```rust
        self.update_l2_gas_price(height, l2_gas_used);
        self.record_fee_proposal(height, init.fee_proposal_fri);
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L72-76)
```rust
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L137-139)
```rust
    // Price should not realistically exceed u128::MAX, bound to avoid theoretical overflow.
    let adjusted_price = u128::try_from(adjusted_price_u256).unwrap_or(u128::MAX);
    GasPrice(max(adjusted_price, min_gas_price.0))
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_0.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 3200000000,
    "max_block_size": 4000000000,
    "min_gas_price": "0xb2d05e00",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L276-282)
```rust
    pub min_l1_gas_price_wei: u128,
    /// The maximum L1 gas price in wei.
    pub max_l1_gas_price_wei: u128,
    /// The minimum L1 data gas price in wei.
    pub min_l1_data_gas_price_wei: u128,
    /// The maximum L1 data gas price in wei.
    pub max_l1_data_gas_price_wei: u128,
```
