### Title
Unconstrained `fee_proposal_fri` During Startup Window Allows Arbitrary Fee Market Manipulation — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

During the first `window_size` blocks after V0_14_3 activation, `is_proposal_init_valid` skips all bounds enforcement on `fee_proposal_fri` because `fee_actual` is `None`. A malicious proposer can set `fee_proposal_fri` to any value (e.g., `u128::MAX`), which validators accept unconditionally, commit to the chain, and store in `fee_proposals_window`, permanently skewing future `fee_actual` (the median-based base fee) for all subsequent blocks.

### Finding Description

`is_proposal_init_valid` in `validate_proposal.rs` validates `fee_proposal_fri` against `fee_actual` only when `fee_actual` is `Some`:

```rust
// Validate fee_proposal is within the configured margin of fee_actual.
// During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{
    // bounds check
}
``` [1](#0-0) 

`fee_actual` is `None` whenever the `fee_proposals_window` has fewer than `window_size` entries — i.e., for the first `window_size` blocks after V0_14_3 activation: [2](#0-1) 

The comment in `compute_proposer_fee_proposal` states *"the validator derives the same fallback so both sides agree"*, implying the validator should enforce `fee_proposal_fri == l2_gas_price` during startup: [3](#0-2) 

But the validator does **not** derive or enforce any fallback. It simply skips the check. The proposer is expected to freeze at `l2_gas_price`, but nothing prevents it from emitting any arbitrary value.

The committed `fee_proposal_fri` is then stored in `fee_proposals_window` via `record_fee_proposal`: [4](#0-3) 

And `compute_fee_actual` uses those stored values as the median base fee for all future blocks: [5](#0-4) 

### Impact Explanation

A malicious proposer who wins even one slot during the startup window can set `fee_proposal_fri = u128::MAX`. This value:

1. Passes `is_proposal_init_valid` (no bounds check when `fee_actual` is `None`)
2. Is bound into `ProposalCommitment` via `Poseidon(partial_block_hash, fee_proposal_fri)` — consensus signs over it
3. Is stored in `fee_proposals_window` after `decision_reached`
4. Shifts the median `fee_actual` for the next `window_size` blocks

Once `fee_actual` is inflated, all future proposers are forced to propose fees within a margin of the manipulated value, and the validator enforces those inflated bounds. This constitutes **incorrect fee/gas accounting with direct economic impact** on every transaction in subsequent blocks. [6](#0-5) 

### Likelihood Explanation

The window is bounded to the first `window_size` blocks after V0_14_3 activation — a finite, predictable period. Any validator who proposes during that window can exploit this. In a BFT system with rotating proposers, a single malicious validator with non-trivial stake has a meaningful probability of proposing at least one block during the startup window.

### Recommendation

In `is_proposal_init_valid`, when `fee_actual` is `None`, validate that `fee_proposal_fri` equals the locally-derived fallback (`l2_gas_price`), matching the invariant the proposer is already expected to follow:

```rust
if fee_actual is None {
    // Enforce the fallback: proposer must freeze at l2_gas_price
    if fee_proposal != proposal_init_validation.l2_gas_price_fri {
        return Err(InvalidProposalInit(..., "fee_proposal must equal l2_gas_price during startup"));
    }
}
```

`ProposalInitValidation` already carries `l2_gas_price_fri`, so no new data needs to be threaded through. [7](#0-6) 

### Proof of Concept

1. Network activates V0_14_3; `fee_proposals_window` is empty → `compute_fee_actual` returns `None`.
2. Malicious validator wins a proposal slot; sets `init.fee_proposal_fri = Some(GasPrice(u128::MAX))`.
3. `is_proposal_init_valid` reaches the `if let (Some(fee_actual), Some(fee_proposal))` guard — `fee_actual` is `None`, so the entire block is skipped. Validation returns `Ok(())`.
4. `initiate_validation` sends the block to the batcher with the manipulated `fee_proposal_fri` embedded in `block_info`.
5. `ProposalFin` comparison passes (commitment is `Poseidon(partial, u128::MAX)`, both sides compute the same value from the received init).
6. `decision_reached` → `record_fee_proposal(height, Some(u128::MAX))` → stored in window.
7. For the next `window_size` blocks, `compute_fee_actual` returns a median skewed toward `u128::MAX`.
8. All future `fee_proposal_fri` bounds checks enforce margins around the manipulated `fee_actual`, locking the fee market at an attacker-chosen level.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L75-85)
```rust
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub block_timestamp_window_seconds: u64,
    pub previous_proposal_init: Option<PreviousProposalInitInfo>,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub l2_gas_price_fri: GasPrice,
    pub starknet_version: StarknetVersion,
    /// fee_actual from the sliding window. `None` until the window has accumulated
    /// `fee_proposal_window_size` entries (startup / near-genesis).
    pub fee_actual: Option<GasPrice>,
}
```

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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L468-482)
```rust
    /// Compute the proposer's fee_proposal: clamp the oracle's `fee_target` to a margin around
    /// `fee_actual`. When `fee_actual` is `None` (window incomplete), freeze at `l2_gas_price`; the
    /// validator derives the same fallback so both sides agree.
    async fn compute_proposer_fee_proposal(
        &self,
        fee_actual: Option<GasPrice>,
        timestamp: u64,
        target_atto_usd_per_l2_gas: u128,
    ) -> GasPrice {
        SNIP35_FEE_TARGET_ATTO_USD.set_lossy(target_atto_usd_per_l2_gas);
        let Some(fee_actual) = fee_actual else {
            warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
            SNIP35_FEE_PROPOSAL_FRI.set_lossy(self.l2_gas_price.0);
            return self.l2_gas_price;
        };
```
