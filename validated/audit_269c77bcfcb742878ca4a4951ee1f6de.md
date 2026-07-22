### Title
Unconstrained `fee_proposal_fri` During Startup Window Enables Fee Market Manipulation - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`is_proposal_init_valid` intentionally skips all value-level bounds enforcement on `fee_proposal_fri` when `fee_actual` is `None` (the startup/genesis window). The validator only checks that the field is *present* for V0_14_3+, but never checks that its value equals the locally-derived `l2_gas_price` fallback that an honest proposer is required to use. A malicious proposer can therefore publish any `fee_proposal_fri` value during the first `window_size` blocks, which is accepted by every validator, committed to consensus, and stored in `fee_proposals_window`. Those poisoned entries then skew every subsequent `fee_actual` median computation for the next `window_size` blocks, manipulating the `l2_gas_price` floor for all users.

### Finding Description

In `is_proposal_init_valid` the fee-proposal bounds check is guarded by an `if let` that fires only when `fee_actual` is `Some`:

```rust
// Validate fee_proposal is within the configured margin of fee_actual.
// During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{
    let (lower_bound, upper_bound) = fee_proposal_bounds(...);
    if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound { ... }
}
``` [1](#0-0) 

When `fee_actual` is `None` (height < `window_size`), the only check applied to `fee_proposal_fri` is that it is `Some` for V0_14_3+: [2](#0-1) 

The honest proposer is required to freeze `fee_proposal_fri` at `self.l2_gas_price` when `fee_actual` is `None`: [3](#0-2) 

But the validator never enforces this. `proposal_init_validation.l2_gas_price_fri` (the locally-derived value) is present in the validation struct and is already used to check `init_proposed.l2_gas_price_fri`: [4](#0-3) 

Yet the analogous check `init_proposed.fee_proposal_fri == l2_gas_price_fri` is absent for the startup window.

The `ProposalFinMismatch` guard does not close this gap. The validator computes `batcher_block_commitment` using the proposer-supplied `fee_proposal_fri` from `args.init`: [5](#0-4) 

and then compares it against `received_fin.proposal_commitment`, which the same proposer also controls. A consistent (but arbitrary) `(fee_proposal_fri, commitment)` pair always passes this check.

After consensus, the accepted `fee_proposal_fri` is stored in `fee_proposals_window` via `record_fee_proposal`: [6](#0-5) 

`compute_fee_actual` then computes the median over this window: [7](#0-6) 

The resulting `fee_actual` is used as a floor for `l2_gas_price` in subsequent blocks.

### Impact Explanation

A malicious proposer who wins the leader election during the first `window_size` blocks (or after a restart that clears the window) can set `fee_proposal_fri` to an extreme value (e.g., `u128::MAX` or `1`). Every validator accepts this value because no bounds are enforced when `fee_actual` is `None`. The poisoned entries persist in `fee_proposals_window` and skew the `fee_actual` median for the next `window_size` blocks, forcing `l2_gas_price` to an artificially high or low floor. This constitutes incorrect fee/gas accounting with direct economic impact on all users transacting during that period.

### Likelihood Explanation

The startup window is a deterministic, predictable phase: it occurs at genesis and after any restart or revert that clears the window. In a decentralized Tendermint-style consensus, proposer election is round-robin or stake-weighted, so any validator can be the proposer during this window. The attack requires no special privilege beyond winning a normal proposer slot.

### Recommendation

When `fee_actual` is `None`, enforce that `init_proposed.fee_proposal_fri == proposal_init_validation.l2_gas_price_fri` (the locally-derived fallback). This mirrors the existing exact-equality check on `l2_gas_price_fri` and closes the gap without changing the intended behavior for the normal (window-complete) case:

```rust
if fee_actual.is_none() {
    if let Some(fee_proposal) = init_proposed.fee_proposal_fri {
        if fee_proposal != proposal_init_validation.l2_gas_price_fri {
            return Err(ValidateProposalError::InvalidProposalInit(...));
        }
    }
}
```

### Proof of Concept

1. Network starts at height 0 (or restarts); `fee_proposals_window` is empty → `compute_fee_actual` returns `None` for all heights < `window_size`.
2. Malicious node wins proposer slot at height 0. It constructs `ProposalInit` with `fee_proposal_fri = Some(GasPrice(u128::MAX))`.
3. `is_proposal_init_valid` is called. `proposal_init_validation.fee_actual` is `None`. The `if let (Some(fee_actual), Some(fee_proposal))` guard does not fire. The value `u128::MAX` passes unchecked.
4. `initiate_validation` proceeds; batcher executes the block normally.
5. `handle_proposal_part` computes `batcher_block_commitment = proposal_commitment_from(partial_block_hash, Some(u128::MAX))`. The proposer's `ProposalFin.proposal_commitment` was constructed with the same value, so `built_block == received_fin.proposal_commitment` holds. [8](#0-7) 

6. `validate_proposal` returns `Ok`. `record_fee_proposal(height_0, Some(GasPrice(u128::MAX)))` is called. [9](#0-8) 

7. After `window_size` such blocks, `compute_fee_actual` returns a median dominated by `u128::MAX`. `calculate_next_l2_gas_price_for_fin` uses this as a floor, forcing `l2_gas_price` to `u128::MAX` for all subsequent blocks, making every transaction prohibitively expensive.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-247)
```rust
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L312-321)
```rust
    if !(init_proposed.height == proposal_init_validation.height
        && init_proposed.l1_da_mode == proposal_init_validation.l1_da_mode
        && init_proposed.l2_gas_price_fri == proposal_init_validation.l2_gas_price_fri)
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            "ProposalInit validation failed".to_string(),
        ));
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L370-394)
```rust
    // fee_proposal is required iff Starknet version >= V0_14_3.
    let fee_proposal_required = init_proposed.starknet_version >= StarknetVersion::V0_14_3;
    match (init_proposed.fee_proposal_fri, fee_proposal_required) {
        (Some(_), false) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal must be absent before V0_14_3, got Some at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        (None, true) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal is required at V0_14_3+, got None at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        _ => {}
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L582-585)
```rust
            let batcher_block_commitment = proposal_commitment_from(
                finished_info.proposal_commitment.partial_block_hash,
                fee_proposal,
            );
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L308-310)
```rust
    fn record_fee_proposal(&mut self, height: BlockNumber, fee_proposal_fri: Option<GasPrice>) {
        self.fee_proposals_window.insert(height, fee_proposal_fri);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L478-482)
```rust
        let Some(fee_actual) = fee_actual else {
            warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
            SNIP35_FEE_PROPOSAL_FRI.set_lossy(self.l2_gas_price.0);
            return self.l2_gas_price;
        };
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L518-518)
```rust
        self.record_fee_proposal(height, init.fee_proposal_fri);
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
