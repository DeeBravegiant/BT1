### Title
Unbounded `fee_proposal_fri` accepted during startup window poisons `fee_actual` for subsequent blocks — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

During the first `fee_proposal_window_size` (10) blocks after Starknet V0_14_3 activation — or during any transition period where the sliding window contains pre-V0_14_3 blocks — `is_proposal_init_valid` unconditionally skips the `fee_proposal_fri` bounds check because `fee_actual` is `None`. A malicious proposer can set `fee_proposal_fri` to any arbitrary value (e.g., `u128::MAX`) during this window. Those values are committed into the `ProposalCommitment`, stored in `fee_proposals_window`, and used to compute `fee_actual` for all subsequent blocks, permanently distorting the L2 gas price.

### Finding Description

In `is_proposal_init_valid`, the bounds check is guarded by a pattern match that silently passes when `fee_actual` is `None`:

```rust
// Validate fee_proposal is within the configured margin of fee_actual.
// During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{
    // ... bounds check only executes here
}
``` [1](#0-0) 

`fee_actual` is `None` whenever `compute_fee_actual` returns `None`, which occurs when `height < window_size` (10 blocks near genesis) or when any block in the window carries `fee_proposal_fri = None` (pre-V0_14_3 blocks):

```rust
let Some(start) = height.0.checked_sub(window_size) else {
    warn!("Cannot compute fee_actual for height {height}: height is below window_size ({window_size})");
    return None;
};
// ...
Some(None) | None => {
    warn!("Cannot compute fee_actual for height {height}: fee_proposals_window has no recorded fee_proposal for height {source_height}");
    return None;
}
``` [2](#0-1) 

The `fee_proposal_window_size` is 10 across all versioned constants: [3](#0-2) 

The proposer's own code correctly falls back to `l2_gas_price` when `fee_actual` is `None`, and the comment explicitly states the validator should agree:

```rust
let Some(fee_actual) = fee_actual else {
    warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
    return self.l2_gas_price;  // proposer freezes here
};
``` [4](#0-3) 

But the validator does **not** enforce this fallback — it accepts any `fee_proposal_fri` value without restriction during the startup window.

After each committed block, `finalize_decision` unconditionally records the proposer-supplied value into the sliding window:

```rust
self.record_fee_proposal(height, init.fee_proposal_fri);
``` [5](#0-4) 

Once the window fills (block 10+), `compute_fee_actual` computes the median of those stored values. If a malicious proposer injected `u128::MAX` into more than 5 of the 10 startup blocks, the median becomes `u128::MAX`. That value is then passed as `fee_actual` to `calculate_next_l2_gas_price_for_fin`, which uses it as a floor for `l2_gas_price`: [6](#0-5) 

The corrupted `fee_proposal_fri` is also bound into the `ProposalCommitment` via Poseidon hash, so all validators accept the block:

```rust
pub(crate) fn proposal_commitment_from(partial: PartialBlockHash, fee_proposal: Option<GasPrice>) -> ProposalCommitment {
    let Some(fee_proposal) = fee_proposal else { return ProposalCommitment(partial.0); };
    ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
}
``` [7](#0-6) 

And the corrupted value is forwarded to the centralized cende pipeline: [8](#0-7) 

### Impact Explanation

A malicious proposer selected during the 10-block startup window (at genesis or during the V0_14_2→V0_14_3 transition) can set `fee_proposal_fri = u128::MAX` for each block they propose. If they control more than 5 of the 10 window blocks, the median `fee_actual` becomes `u128::MAX`. This permanently floors `l2_gas_price` at `u128::MAX` for all subsequent blocks, making every transaction economically unexecutable. Even controlling fewer blocks skews the median upward, causing incorrect fee accounting with lasting economic impact. The corrupted value is also committed into the `ProposalCommitment` hash and the cende blob, so the wrong value propagates to L1 anchoring and the centralized recorder.

This matches: **Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

### Likelihood Explanation

The attack window is fixed and predictable: exactly the first 10 blocks after V0_14_3 activates (or after any protocol upgrade that resets the window). In Tendermint-style consensus the proposer schedule is deterministic and known in advance, so a malicious validator can plan to be selected during this window. The transition from V0_14_2 to V0_14_3 is the most realistic trigger because the window contains pre-V0_14_3 `None` entries, keeping `fee_actual = None` for the first 10 V0_14_3 blocks regardless of chain height.

### Recommendation

When `fee_actual` is `None`, the validator should enforce that `fee_proposal_fri` equals `l2_gas_price` (the same fallback the proposer uses), rather than accepting any value. Concretely, in `is_proposal_init_valid`, replace the silent skip with an explicit check:

```rust
match (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri) {
    (Some(fee_actual), Some(fee_proposal)) => {
        // existing margin check
    }
    (None, Some(fee_proposal)) => {
        // enforce fallback: fee_proposal must equal l2_gas_price
        if fee_proposal != proposal_init_validation.l2_gas_price_fri {
            return Err(InvalidProposalInit(..., "fee_proposal must equal l2_gas_price during startup window"));
        }
    }
    _ => {}
}
```

### Proof of Concept

1. Chain activates V0_14_3 at height 0; `fee_proposals_window` is empty, so `compute_fee_actual` returns `None` for heights 0–9.
2. A malicious validator is selected as proposer for blocks 0–5 (6 of 10 startup blocks).
3. For each of those blocks, the proposer sets `fee_proposal_fri = Some(GasPrice(u128::MAX))` in `ProposalInit`.
4. `is_proposal_init_valid` reaches the `if let (Some(fee_actual), Some(fee_proposal))` guard; since `fee_actual = None`, the entire block is skipped — no rejection.
5. `validate_proposal` returns `Ok(commitment)` where `commitment = Poseidon(partial_block_hash, u128::MAX)`.
6. `finalize_decision` calls `record_fee_proposal(height, Some(u128::MAX))` for each of the 6 blocks.
7. At height 10, `compute_fee_actual` computes the median of `[u128::MAX, u128::MAX, u128::MAX, u128::MAX, u128::MAX, u128::MAX, honest, honest, honest, honest]` = `u128::MAX`.
8. `calculate_next_l2_gas_price_for_fin` receives `fee_actual = Some(u128::MAX)` and floors `l2_gas_price` at `u128::MAX`.
9. All transactions from block 11 onward require fees of `u128::MAX` FRI, rendering the network economically unusable.

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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L61-80)
```rust
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

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_3.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1040000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L607-611)
```rust
                // Forward the proposer's stated fee_proposal_fri (from ProposalInit)
                // to the centralized cende pipeline. The centralized side persists this in
                // its own storage namespace, separate from FeeMarketInfo. Pre-V0_14_3 blocks
                // have `init.fee_proposal_fri == None`.
                fee_proposal_info: FeeProposalInfo { fee_proposal_fri: init.fee_proposal_fri },
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L326-333)
```rust
                let next_l2_gas_price = calculate_next_l2_gas_price_for_fin(
                    args.l2_gas_price,
                    args.build_param.height,
                    info.l2_gas_used,
                    args.override_l2_gas_price_fri,
                    &args.min_l2_gas_price_per_height,
                    args.fee_actual,
                );
```
