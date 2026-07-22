### Title
Unchecked `fee_proposal_fri` During Bootstrap Silently Bypasses Bounds Enforcement, Poisoning Future `fee_actual` and L2 Gas Price — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_proposal_init_valid` skips the `fee_proposal_fri` bounds check whenever `fee_actual` is `None` (the first `fee_proposal_window_size` = 10 blocks). A proposer during this bootstrap window can set `fee_proposal_fri` to any value — including `u128::MAX` — and every validator will accept it. That value is committed into `ProposalCommitment`, stored in `fee_proposals_window`, and later used to compute `fee_actual`, which is the floor passed to `calculate_next_l2_gas_price_for_fin`. Poisoning more than half the bootstrap window shifts `fee_actual` to an attacker-chosen value, permanently distorting the L2 gas price for all subsequent blocks.

---

### Finding Description

**Root cause — incomplete guard in `is_proposal_init_valid`**

```rust
// crates/apollo_consensus_orchestrator/src/validate_proposal.rs  lines 396-416
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
        return Err(ValidateProposalError::InvalidProposalInit(...));
    }
}
```

When `proposal_init_validation.fee_actual` is `None` the entire `if let` arm is skipped. No fallback check, no clamping, no rejection — any `fee_proposal_fri` value passes. [1](#0-0) 

**`fee_actual` is `None` for exactly the first `window_size` blocks**

`ProposalInitValidation.fee_actual` is computed by `compute_fee_actual`, which returns `None` whenever `height < window_size` (currently 10):

```rust
let Some(start) = height.0.checked_sub(window_size) else {
    warn!("Cannot compute fee_actual for height {height}: height is below window_size ({window_size})");
    return None;
};
``` [2](#0-1) 

Both `validate_proposal` call-sites in `SequencerConsensusContext` pass this `None` directly into `ProposalInitValidation`: [3](#0-2) [4](#0-3) 

**Accepted value is committed and stored**

After `is_proposal_init_valid` returns `Ok`, the arbitrary `fee_proposal_fri` is:

1. Hashed into `ProposalCommitment` — `Poseidon(partial_block_hash, fee_proposal_fri)` — which consensus signs over:

```rust
pub(crate) fn proposal_commitment_from(partial: PartialBlockHash, fee_proposal: Option<GasPrice>) -> ProposalCommitment {
    let Some(fee_proposal) = fee_proposal else { return ProposalCommitment(partial.0); };
    ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
}
``` [5](#0-4) 

2. Recorded into `fee_proposals_window` on `decision_reached`:

```rust
self.record_fee_proposal(height, init.fee_proposal_fri);
``` [6](#0-5) 

**Poisoned window propagates to `fee_actual` and then to the L2 gas price**

After the bootstrap window fills, `compute_fee_actual` returns the median of the stored values. If an attacker controls the proposer role for ≥ 6 of the first 10 blocks and sets `fee_proposal_fri = u128::MAX` each time, the median is `u128::MAX`. This `fee_actual` is then passed directly to `calculate_next_l2_gas_price_for_fin`:

```rust
let next_l2_gas_price = calculate_next_l2_gas_price_for_fin(
    args.l2_gas_price,
    args.build_param.height,
    info.l2_gas_used,
    args.override_l2_gas_price_fri,
    &args.min_l2_gas_price_per_height,
    args.fee_actual,   // ← poisoned
);
``` [7](#0-6) 

The `fee_actual` also bounds every future proposer's `fee_proposal_fri` via `fee_proposal_bounds`, so the poisoned value propagates forward through the sliding window indefinitely. [8](#0-7) 

---

### Impact Explanation

This matches **"Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."**

- `fee_actual` is the floor fed into `calculate_next_l2_gas_price_for_fin`, which sets the L2 gas price for all subsequent blocks.
- Setting `fee_actual = u128::MAX` drives the L2 gas price to its maximum, making every transaction prohibitively expensive.
- Setting `fee_actual = 1` drives the L2 gas price to its minimum, enabling spam at near-zero cost.
- The poisoned `ProposalCommitment` values are consensus-signed and stored on-chain, making the manipulation permanent and irrefutable.

---

### Likelihood Explanation

The attacker must be selected as proposer for a majority (≥ 6 of 10) of the bootstrap blocks. In a permissioned or early-stage validator set this is realistic; in a large decentralized set it is harder but not impossible (Sybil attack, validator collusion, or simply being an early participant). The window is exactly 10 blocks — a narrow but deterministic target. No special key material or privileged API access is required beyond normal validator participation.

---

### Recommendation

Replace the silent skip with an explicit fallback bound during bootstrap. When `fee_actual` is `None`, clamp `fee_proposal_fri` against a deterministic reference — for example, the current `l2_gas_price` (which both proposer and validator already agree on) — using the same `fee_proposal_margin_ppt`:

```rust
let effective_fee_actual = proposal_init_validation.fee_actual
    .unwrap_or(/* agreed fallback, e.g. l2_gas_price_fri from ProposalInitValidation */);
let (lower_bound, upper_bound) = fee_proposal_bounds(
    effective_fee_actual,
    VersionedConstants::latest_constants().fee_proposal_margin_ppt,
);
if let Some(fee_proposal) = init_proposed.fee_proposal_fri {
    if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
        return Err(...);
    }
}
```

This closes the bootstrap window without breaking the existing post-bootstrap logic.

---

### Proof of Concept

1. Join the network as a validator before block 0.
2. For each of the first 10 blocks where you are selected as proposer, construct a `ProposalInit` with `fee_proposal_fri = Some(GasPrice(u128::MAX))`.
3. Broadcast the proposal. Every validator calls `is_proposal_init_valid`; because `fee_actual` is `None` for all heights `< 10`, the bounds check arm is never entered and the proposal is accepted.
4. `validate_proposal` calls `valid_proposals.insert_proposal(args.init, ...)`, which calls `proposal_commitment_from(partial_block_hash, Some(u128::MAX))` — the extreme value is hashed into the signed commitment.
5. On `decision_reached`, `self.record_fee_proposal(height, Some(GasPrice(u128::MAX)))` stores the value in `fee_proposals_window`.
6. At block 10, `compute_fee_actual` computes the median of the window. If ≥ 6 entries are `u128::MAX`, the median is `u128::MAX`.
7. `calculate_next_l2_gas_price_for_fin(..., fee_actual = Some(GasPrice(u128::MAX)))` sets the L2 gas price to its maximum.
8. All transactions from block 11 onward require fees proportional to `u128::MAX`, effectively halting the network.

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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L144-151)
```rust
pub(crate) fn fee_proposal_bounds(fee_actual: GasPrice, margin_ppt: u128) -> (u128, u128) {
    let denom = U256::from(PPT_DENOMINATOR);
    let scaled = denom + U256::from(margin_ppt);
    let fee_actual_u256 = U256::from(fee_actual.0);
    let upper = u128::try_from(fee_actual_u256 * scaled / denom).unwrap_or(u128::MAX);
    let lower = u128::try_from(fee_actual_u256 * denom / scaled).unwrap_or(0);
    (lower, upper)
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1193-1198)
```rust
            fee_actual: compute_fee_actual(
                &self.fee_proposals_window,
                init.height,
                VersionedConstants::latest_constants().fee_proposal_window_size,
            ),
        };
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
