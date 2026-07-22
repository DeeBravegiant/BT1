### Title
Validator Skips `fee_proposal_fri` Bounds Enforcement During Window Initialization, Allowing Arbitrary L2 Gas Price Seeding — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

In `is_proposal_init_valid`, the `fee_proposal_fri` bounds check is gated on `fee_actual` being `Some`. During the first `window_size` blocks — when `fee_actual` is `None` because the sliding window has not yet accumulated enough entries — the check is silently skipped. A proposer can therefore publish any arbitrary `fee_proposal_fri` value in `ProposalInit` during this period, validators accept it unconditionally, and the value is permanently recorded in `fee_proposals_window`. Once the window fills, those seeded values drive `fee_actual`, which in turn bounds every subsequent proposer's `fee_proposal` and sets the L2 gas price for future blocks.

---

### Finding Description

**The zero-guard in `is_proposal_init_valid`**

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
        return Err(...);
    }
}
``` [1](#0-0) 

`fee_actual` is computed by `compute_fee_actual`, which returns `None` whenever `height < window_size` (the window has not yet accumulated `window_size` entries):

```rust
let Some(start) = height.0.checked_sub(window_size) else {
    warn!("Cannot compute fee_actual for height {height}: height is below window_size ({window_size})");
    return None;
};
``` [2](#0-1) 

The validator constructs `ProposalInitValidation` with this `None` value and passes it to `is_proposal_init_valid`:

```rust
fee_actual: compute_fee_actual(
    &self.fee_proposals_window,
    init.height,
    VersionedConstants::latest_constants().fee_proposal_window_size,
),
``` [3](#0-2) 

**The proposer side correctly freezes at `l2_gas_price` when `fee_actual` is `None`:**

```rust
let Some(fee_actual) = fee_actual else {
    warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
    return self.l2_gas_price;
};
``` [4](#0-3) 

The validator does **not** mirror this constraint. Any `fee_proposal_fri` value — including `u128::MAX` or `0` — passes `is_proposal_init_valid` during the window period.

**The accepted value is permanently recorded and propagates forward:**

```rust
self.record_fee_proposal(height, init.fee_proposal_fri);
``` [5](#0-4) 

```rust
fn record_fee_proposal(&mut self, height: BlockNumber, fee_proposal_fri: Option<GasPrice>) {
    self.fee_proposals_window.insert(height, fee_proposal_fri);
}
``` [6](#0-5) 

Once `window_size` blocks have passed, `compute_fee_actual` computes the median of the window — including the seeded extreme values — and returns it as `fee_actual`. All subsequent `fee_proposal` bounds and the L2 gas price for future blocks are derived from this corrupted median.

---

### Impact Explanation

`fee_actual` is the sole anchor for the `fee_proposal` geometric bounds enforced on every subsequent proposer. The L2 gas price for each block is derived from `fee_actual` via `calculate_next_l2_gas_price_for_fin`. By seeding the window with extreme values during the unchecked startup period, an attacker can:

- Drive `fee_actual` to near-zero, collapsing the L2 gas price and making transactions effectively free for a window of blocks.
- Drive `fee_actual` to `u128::MAX`, making the L2 gas price prohibitively high and blocking all user transactions.

Both outcomes constitute an incorrect fee/gas accounting effect with direct economic impact, matching the "Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact" criterion.

---

### Likelihood Explanation

The vulnerable window is the first `window_size` blocks after genesis or after a network restart that resets the window. Any consensus participant who becomes a proposer during those blocks — which is a normal, unprivileged role in Tendermint-based consensus — can exploit this. No special privilege beyond being selected as proposer is required. The window is finite but the damage (corrupted `fee_proposals_window` entries) persists for the lifetime of those entries in the sliding window.

---

### Recommendation

When `fee_actual` is `None`, the validator should enforce that `fee_proposal_fri` equals the locally-computed fallback (`l2_gas_price`), mirroring the proposer's own freeze behavior. Concretely, replace the silent skip with an explicit check:

```rust
match (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri) {
    (Some(fee_actual), Some(fee_proposal)) => {
        // existing bounds check
    }
    (None, Some(fee_proposal)) => {
        // Window not yet full: proposer must freeze at l2_gas_price.
        if fee_proposal != proposal_init_validation.l2_gas_price_fri {
            return Err(ValidateProposalError::InvalidProposalInit(...));
        }
    }
    _ => {}
}
```

---

### Proof of Concept

1. Network starts at block 0 (or restarts). `fee_proposals_window` is empty; `fee_actual` is `None` for all heights `< window_size`.
2. A proposer at height 0 constructs `ProposalInit` with `fee_proposal_fri = Some(GasPrice(u128::MAX))`.
3. Every validator calls `is_proposal_init_valid`. The `if let (Some(fee_actual), Some(fee_proposal))` pattern does not match because `fee_actual` is `None`. The bounds check is skipped. The proposal is accepted.
4. `finalize_decision` calls `record_fee_proposal(0, Some(GasPrice(u128::MAX)))`, inserting the extreme value into `fee_proposals_window`.
5. Steps 2–4 repeat for heights 1 through `window_size - 1` (each proposer can independently set extreme values; validators accept all of them).
6. At height `window_size`, `compute_fee_actual` now has a full window of `u128::MAX` entries. The median is `u128::MAX`. `fee_actual = Some(GasPrice(u128::MAX))`.
7. `calculate_next_l2_gas_price_for_fin` uses this `fee_actual` to set the L2 gas price for block `window_size` to `u128::MAX`, making all subsequent transactions prohibitively expensive.

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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L61-67)
```rust
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!(
            "Cannot compute fee_actual for height {height}: height is below window_size \
             ({window_size})"
        );
        return None;
    };
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L895-900)
```rust
                    fee_actual: compute_fee_actual(
                        &self.fee_proposals_window,
                        init.height,
                        VersionedConstants::latest_constants().fee_proposal_window_size,
                    ),
                };
```
