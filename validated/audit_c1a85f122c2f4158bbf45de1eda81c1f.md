### Title
Unconstrained `fee_proposal_fri` During Startup Window Allows Proposer to Corrupt Future `fee_actual` and `l2_gas_price` — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`is_proposal_init_valid` enforces that `fee_proposal_fri` lies within a geometric margin of `fee_actual`. The check is wrapped in `if let (Some(fee_actual), Some(fee_proposal)) = (...)`, so it is silently skipped whenever `fee_actual` is `None`. `fee_actual` is `None` for the first `fee_proposal_window_size` blocks (startup / near-genesis). During that window a proposer can broadcast any `fee_proposal_fri` value — including `u128::MAX` or `0` — and every validator will accept it. Those values are then recorded in `fee_proposals_window`, become the median that drives `fee_actual` once the window fills, and propagate into the `l2_gas_price` used for all subsequent blocks.

### Finding Description

In `is_proposal_init_valid` the bounds check reads:

```rust
// Validate fee_proposal is within the configured margin of fee_actual.
// During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{
    ...
    if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
        return Err(ValidateProposalError::InvalidProposalInit(...));
    }
}
``` [1](#0-0) 

`proposal_init_validation.fee_actual` is populated by `compute_fee_actual`, which returns `None` whenever `height < window_size` or any entry in the sliding window is missing:

```rust
let Some(start) = height.0.checked_sub(window_size) else {
    warn!("Cannot compute fee_actual for height {height}: height is below window_size ({window_size})");
    return None;
};
``` [2](#0-1) 

Both `validate_proposal` call-sites pass `fee_actual` from `compute_fee_actual` directly into `ProposalInitValidation`: [3](#0-2) 

When `fee_actual` is `None` the entire bounds block is skipped. The proposer-supplied `fee_proposal_fri` is accepted unconditionally, then committed to the chain via `record_fee_proposal`:

```rust
self.record_fee_proposal(height, init.fee_proposal_fri);
``` [4](#0-3) 

Once `window_size` such blocks have been decided, `compute_fee_actual` returns the median of the recorded values. That median becomes the floor for `calculate_next_l2_gas_price_for_fin`, which sets `self.l2_gas_price` — the value every validator enforces as the required `l2_gas_price_fri` in every subsequent `ProposalInit`.

The `fee_proposal_fri` is also hashed into the `ProposalCommitment` that consensus signs over:

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
``` [5](#0-4) 

Because the commitment binds the manipulated value, the validator's locally-computed commitment matches the proposer's, so `ProposalFinMismatch` is never triggered.

The structural parallel to the external report is exact:

| External (MANTRA) | Sequencer |
|---|---|
| `if let Some(slippage_tolerance)` — check skipped when `None` | `if let (Some(fee_actual), Some(fee_proposal))` — check skipped when `fee_actual` is `None` |
| Any caller bypasses by omitting slippage | Any proposer during startup bypasses because window is incomplete |
| Broken pool invariant (`x*y=k`) | Broken fee-market invariant (unconstrained `fee_actual` seed) |

### Impact Explanation

A proposer that is selected during the first `window_size` blocks can record `fee_proposal_fri = u128::MAX` (or `0`) for each block it proposes. Once the window fills, `fee_actual` equals the median of those extreme values. `l2_gas_price` for all subsequent blocks is derived from that corrupted `fee_actual`. Validators enforce the corrupted `l2_gas_price_fri` in every future `ProposalInit`, so:

- Setting `fee_proposal_fri = u128::MAX` → future `l2_gas_price = u128::MAX` → every user transaction fails with insufficient resource bounds (economic DoS).
- Setting `fee_proposal_fri = 0` → future `l2_gas_price = 0` → the network is permanently underpriced, enabling spam and draining sequencer revenue.

This is an incorrect fee/gas accounting effect with direct economic impact, matching the "Critical" impact tier.

### Likelihood Explanation

The trigger requires the attacker to be selected as block proposer for a sufficient fraction of the first `window_size` blocks. In a Tendermint-style validator set this is proportional to stake weight, not a special privilege. A validator holding a plurality of stake during the genesis window — a realistic scenario for early-stage networks or testnets — can execute this without any out-of-band access. The window is bounded (`window_size` blocks), but the damage persists indefinitely once the window is poisoned.

### Recommendation

Add an absolute fallback bound when `fee_actual` is `None`. For example, clamp `fee_proposal_fri` to `[min_gas_price, max_gas_price]` from `VersionedConstants` even during the startup window:

```rust
// When fee_actual is unavailable, enforce absolute bounds instead.
if let Some(fee_proposal) = init_proposed.fee_proposal_fri {
    let (lower, upper) = if let Some(fee_actual) = proposal_init_validation.fee_actual {
        fee_proposal_bounds(fee_actual, VersionedConstants::latest_constants().fee_proposal_margin_ppt)
    } else {
        (versioned_constants.min_gas_price.0, versioned_constants.max_gas_price.0)
    };
    if fee_proposal.0 < lower || fee_proposal.0 > upper {
        return Err(ValidateProposalError::InvalidProposalInit(...));
    }
}
```

### Proof of Concept

1. Network launches at height 0 with `window_size = W`.
2. Attacker controls the proposer role for heights `0..W` (e.g., holds majority stake at genesis).
3. For each block, attacker sets `fee_proposal_fri = u128::MAX` in `ProposalInit`.
4. `is_proposal_init_valid` reaches the bounds check: `fee_actual = None` → `if let` does not match → check is skipped → proposal accepted.
5. `finalize_decision` calls `record_fee_proposal(height, Some(u128::MAX))` for each height.
6. At height `W`, `compute_fee_actual` returns `Some(u128::MAX)` (median of `W` identical values).
7. `calculate_next_l2_gas_price_for_fin(..., fee_actual = Some(u128::MAX))` sets `self.l2_gas_price = u128::MAX`.
8. Every subsequent `ProposalInit` must carry `l2_gas_price_fri = u128::MAX`; every user transaction specifying any finite resource bound is rejected. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L396-418)
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

    Ok(())
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L471-494)
```rust
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
        SNIP35_FEE_ACTUAL_FRI.set_lossy(fee_actual.0);

        let fee_target = self.resolve_fee_target(timestamp, target_atto_usd_per_l2_gas).await;

        let proposal = compute_fee_proposal(
            fee_target,
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
        SNIP35_FEE_PROPOSAL_FRI.set_lossy(proposal.0);
        proposal
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L518-518)
```rust
        self.record_fee_proposal(height, init.fee_proposal_fri);
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L895-900)
```rust
                    fee_actual: compute_fee_actual(
                        &self.fee_proposals_window,
                        init.height,
                        VersionedConstants::latest_constants().fee_proposal_window_size,
                    ),
                };
```
