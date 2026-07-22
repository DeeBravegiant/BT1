### Title
`fee_proposal` Bounds Check Unconditionally Skipped When `fee_actual` Is `None` During Startup Window — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`is_proposal_init_valid` enforces that a proposer's `fee_proposal_fri` lies within a geometric band around the locally-computed `fee_actual`. However, the entire bounds check is wrapped in an `if let (Some(fee_actual), Some(fee_proposal))` guard. When `fee_actual` is `None` — which is guaranteed for the first `fee_proposal_window_size` (default: 10) blocks of any new network — the bounds check is silently skipped. A malicious proposer who controls any block during this startup window can publish an arbitrary `fee_proposal_fri` value, validators will accept it without any range enforcement, and the value is permanently stored in `fee_proposals_window`. After the window fills, `fee_actual` is computed as the median of these stored values, anchoring all future `fee_proposal` bounds to the attacker-chosen extreme, enabling sustained fee-market manipulation.

### Finding Description

In `is_proposal_init_valid` (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`, lines 396–416):

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
        return Err(ValidateProposalError::InvalidProposalInit(...));
    }
}
``` [1](#0-0) 

The guard `if let (Some(fee_actual), Some(fee_proposal))` means: when `fee_actual` is `None`, the entire bounds check is bypassed. `fee_actual` is `None` whenever `compute_fee_actual` cannot produce a median because the sliding window has fewer than `window_size` entries:

```rust
pub fn compute_fee_actual(
    fee_proposals_window: &BTreeMap<BlockNumber, Option<GasPrice>>,
    height: BlockNumber,
    window_size: u64,
) -> Option<GasPrice> {
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!("Cannot compute fee_actual for height {height}: height is below window_size ...");
        return None;
    };
    ...
}
``` [2](#0-1) 

The `fee_actual` field in `ProposalInitValidation` is documented as `None` until the window has accumulated `fee_proposal_window_size` entries:

```rust
/// fee_actual from the sliding window. `None` until the window has accumulated
/// `fee_proposal_window_size` entries (startup / near-genesis).
pub fee_actual: Option<GasPrice>,
``` [3](#0-2) 

The default `fee_proposal_window_size` is 10 blocks: [4](#0-3) 

During these first 10 blocks, `fee_proposal_fri` is required to be `Some` (enforced for `starknet_version >= V0_14_3`):

```rust
(None, true) => {
    return Err(ValidateProposalError::InvalidProposalInit(...,
        format!("fee_proposal is required at V0_14_3+, got None at version {}",
            init_proposed.starknet_version)));
}
``` [5](#0-4) 

So `fee_proposal_fri` must be present but its value is completely unconstrained during the startup window.

The accepted `fee_proposal_fri` is then:

1. **Hashed into the `ProposalCommitment`** that consensus votes on:

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
``` [6](#0-5) 

2. **Stored in `fee_proposals_window`** and used to compute `fee_actual` for all subsequent blocks once the window fills.

3. **Used in `calculate_next_l2_gas_price_for_fin`** (via `fee_actual`) to set the L2 gas price that governs transaction fees for future blocks.

### Impact Explanation

A malicious proposer who controls any block during the first `window_size` (10) blocks can set `fee_proposal_fri` to an extreme value (e.g., `u128::MAX` or `1`). Validators accept this without bounds checking. After 10 blocks, `fee_actual` is computed as the median of the stored values. If the attacker controls a majority of the startup blocks, the median is anchored to the attacker's chosen extreme. All subsequent `fee_proposal` bounds are derived from this skewed `fee_actual`, allowing the attacker to sustain extreme L2 gas prices (either inflated to extract fees or deflated to undercut the fee market) for the entire network's lifetime, since each round's bounds are anchored to the previous round's `fee_actual`.

This matches the impact category: **Incorrect fee, gas, or resource accounting with economic impact**.

### Likelihood Explanation

On a new network deployment (genesis or after a hard reset), the first 10 blocks are produced by a small, known validator set. Any validator who is scheduled to propose during this window can exploit the bypass. The attack requires no special privileges beyond being a scheduled proposer — a normal network participant role. The window is guaranteed to exist on every new network.

### Recommendation

Replace the `if let (Some(...), Some(...))` guard with an explicit check that rejects proposals with out-of-range `fee_proposal_fri` even when `fee_actual` is unavailable. One approach: during the startup window, enforce that `fee_proposal_fri` equals the local node's own fallback value (`l2_gas_price`), which is the same value an honest proposer would use per `compute_proposer_fee_proposal`. This eliminates the unconstrained window without breaking the bootstrapping logic.

```rust
// Proposed fix sketch:
if let Some(fee_proposal) = init_proposed.fee_proposal_fri {
    match proposal_init_validation.fee_actual {
        Some(fee_actual) => {
            // Normal bounds check
            let (lower, upper) = fee_proposal_bounds(fee_actual, margin_ppt);
            if fee_proposal.0 < lower || fee_proposal.0 > upper {
                return Err(...);
            }
        }
        None => {
            // Startup: enforce equality with the local fallback
            if fee_proposal != proposal_init_validation.l2_gas_price_fri {
                return Err(...);
            }
        }
    }
}
```

### Proof of Concept

1. Deploy a new Starknet network (height 0, `window_size = 10`).
2. As the proposer for block 0, construct a `ProposalInit` with `fee_proposal_fri = Some(GasPrice(u128::MAX))` and `starknet_version = V0_14_3`.
3. Broadcast the proposal. Each validator calls `is_proposal_init_valid`. Since `fee_actual` is `None` (height 0 < window_size 10), the `if let (Some(fee_actual), Some(fee_proposal))` guard does not match and the bounds check is skipped entirely.
4. Validators accept the proposal and vote on the commitment `Poseidon(partial_block_hash, u128::MAX)`.
5. The value `u128::MAX` is stored in `fee_proposals_window[0]`.
6. Repeat for blocks 1–9 (or as many as the attacker controls).
7. At block 10, `compute_fee_actual` computes the median of the stored values. If the attacker controlled ≥ 5 of the first 10 blocks, the median is `u128::MAX`.
8. All subsequent `fee_proposal` bounds are `[u128::MAX / (1 + margin), u128::MAX]`, forcing all future proposers to publish near-maximum fee proposals, extracting maximum fees from all users indefinitely. [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L82-84)
```rust
    /// fee_actual from the sliding window. `None` until the window has accumulated
    /// `fee_proposal_window_size` entries (startup / near-genesis).
    pub fee_actual: Option<GasPrice>,
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L383-394)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L895-900)
```rust
                    fee_actual: compute_fee_actual(
                        &self.fee_proposals_window,
                        init.height,
                        VersionedConstants::latest_constants().fee_proposal_window_size,
                    ),
                };
```
