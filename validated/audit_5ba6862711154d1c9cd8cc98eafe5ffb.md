### Title
Validator Accepts Arbitrary `fee_proposal_fri` During Startup Window, Poisoning `fee_actual` and `l2_gas_price` Floor - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

During the first `window_size` (= 10) blocks, `is_proposal_init_valid` skips the bounds check on `fee_proposal_fri` entirely when `fee_actual` is `None`. A malicious proposer can set `fee_proposal_fri` to any value (e.g., `u128::MAX`). Validators are forced to accept it — there is no rejection path. The committed value feeds into `fee_proposals_window`, which after the window fills becomes `fee_actual`. `fee_actual` is then used as a hard floor for `l2_gas_price` in `calculate_next_l2_gas_price_for_fin`. If a malicious proposer controls a majority of the 10 startup blocks, every subsequent block's `l2_gas_price` is floored at the poisoned value, causing all users to pay extreme fees indefinitely.

---

### Finding Description

**Root cause — missing fallback enforcement in `is_proposal_init_valid`:**

The bounds check on `fee_proposal_fri` is gated on `fee_actual` being `Some`:

```rust
// Validate fee_proposal is within the configured margin of fee_actual.
// During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{ ... }
``` [1](#0-0) 

When `fee_actual` is `None` (the first 10 blocks), the entire bounds check is skipped. The proposer's own fallback is `self.l2_gas_price`:

```rust
let Some(fee_actual) = fee_actual else {
    warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
    return self.l2_gas_price;
};
``` [2](#0-1) 

The comment says "the validator derives the same fallback so both sides agree," but the validator does **not** enforce that `fee_proposal_fri == l2_gas_price` when `fee_actual` is `None`. It simply accepts any value.

**Commitment path — the arbitrary value is cryptographically committed:**

The validator computes `batcher_block_commitment` using the proposer's `fee_proposal_fri` from `ProposalInit`:

```rust
let batcher_block_commitment = proposal_commitment_from(
    finished_info.proposal_commitment.partial_block_hash,
    fee_proposal,  // = args.init.fee_proposal_fri from the wire
);
``` [3](#0-2) 

`proposal_commitment_from` hashes the arbitrary value in:

```rust
ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
``` [4](#0-3) 

The `ProposalFinMismatch` check passes because both proposer and validator use the same `fee_proposal_fri`. The block is committed with the poisoned value.

**Storage path — the value enters `fee_proposals_window`:**

After decision, `record_fee_proposal` stores `init.fee_proposal_fri` into the in-memory window:

```rust
fn record_fee_proposal(&mut self, height: BlockNumber, fee_proposal_fri: Option<GasPrice>) {
    self.fee_proposals_window.insert(height, fee_proposal_fri);
}
``` [5](#0-4) 

It is also persisted to `BlockHeaderWithoutHash.fee_proposal_fri` in state sync storage: [6](#0-5) 

On restart, `initialize_fee_proposals_window` re-reads these values from state sync, so the poison survives restarts. [7](#0-6) 

**Fee market impact — `fee_actual` becomes the `l2_gas_price` floor:**

After 10 blocks, `compute_fee_actual` returns the median of the window. If the malicious proposer controls >5 of the 10 startup blocks and sets `fee_proposal_fri = u128::MAX`, the median is `u128::MAX`. In `calculate_next_l2_gas_price_for_fin`:

```rust
let effective_min = match fee_actual {
    Some(fa) => GasPrice(max(config_min.0, fa.0)),
    None => config_min,
};
calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
``` [8](#0-7) 

`effective_min = u128::MAX` means every future block's `l2_gas_price` is floored at `u128::MAX`. All user transactions pay maximum fees permanently. The `fee_proposal_margin_ppt = 2` (0.2%) rate-limit cannot bring the fee back down because the floor is absolute. [9](#0-8) 

---

### Impact Explanation

A malicious proposer who controls a majority of the 10-block startup window can set `fee_actual` to `u128::MAX`. This becomes the permanent `l2_gas_price` floor for all subsequent blocks. Every Starknet transaction pays maximum fees. The poisoned value survives node restarts (persisted in state sync). This is an incorrect fee/gas accounting effect with direct economic impact on all users — matching the "Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact" scope.

---

### Likelihood Explanation

`window_size = 10`. A malicious proposer needs to be selected for >5 of the first 10 blocks. In a BFT system with 1/3 Byzantine validators, the expected share is ~3 blocks — below the threshold. However: (a) at genesis, validator sets may be small or controlled; (b) the startup window is a one-time, narrow, high-value target; (c) any network with a larger Byzantine fraction (e.g., during early testnet or a coordinated attack) is fully vulnerable. The window of opportunity is small but the consequence is permanent.

---

### Recommendation

When `fee_actual` is `None`, enforce that `fee_proposal_fri` equals the validator's own `l2_gas_price` (the same fallback the proposer uses). Replace the silent skip with an explicit check:

```rust
match (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri) {
    (None, Some(fee_proposal)) => {
        // Startup window: proposer must freeze at l2_gas_price.
        let expected = proposal_init_validation.l2_gas_price_fri;
        if fee_proposal != expected {
            return Err(ValidateProposalError::InvalidProposalInit(...));
        }
    }
    (Some(fee_actual), Some(fee_proposal)) => {
        // Normal path: bounds check.
        ...
    }
    _ => {}
}
```

This closes the missing "cancel path": validators can now reject any proposal whose `fee_proposal_fri` deviates from the agreed startup fallback, preventing startup-window poisoning of the fee market.

---

### Proof of Concept

1. Network launches at genesis (`window_size = 10`, `fee_proposal_margin_ppt = 2`).
2. Malicious proposer is selected for blocks 0–5 (6 of 10 startup blocks).
3. For each of those blocks, the proposer sets `fee_proposal_fri = u128::MAX` in `ProposalInit`.
4. `is_proposal_init_valid` is called; `proposal_init_validation.fee_actual` is `None` (window not yet full); the `if let (Some(fee_actual), Some(fee_proposal))` guard is false; the bounds check is skipped; the proposal is accepted.
5. `proposal_commitment_from(partial_block_hash, u128::MAX)` is computed by both sides; `ProposalFinMismatch` does not trigger; consensus votes on the commitment.
6. `record_fee_proposal(height, Some(GasPrice(u128::MAX)))` stores the poisoned value in `fee_proposals_window` for each of the 6 blocks.
7. At block 10, `compute_fee_actual` computes the median of `[u128::MAX × 6, l2_gas_price × 4]`; the median (5th/6th values when sorted) is `u128::MAX`.
8. `calculate_next_l2_gas_price_for_fin` sets `effective_min = max(config_min, u128::MAX) = u128::MAX`; `calculate_next_base_gas_price` returns `u128::MAX`.
9. All subsequent blocks have `l2_gas_price = u128::MAX`; all user transactions are charged maximum fees permanently.

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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L399-412)
```rust
        let block_header_without_hash = BlockHeaderWithoutHash {
            block_number: height,
            l1_gas_price,
            l1_data_gas_price,
            l2_gas_price,
            l2_gas_consumed: l2_gas_used,
            next_l2_gas_price: self.l2_gas_price,
            sequencer,
            timestamp: BlockTimestamp(init.timestamp),
            l1_da_mode: init.l1_da_mode,
            fee_proposal_fri: init.fee_proposal_fri,
            // TODO(guy.f): Figure out where/if to get the values below from and fill them.
            ..Default::default()
        };
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L478-482)
```rust
        let Some(fee_actual) = fee_actual else {
            warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
            SNIP35_FEE_PROPOSAL_FRI.set_lossy(self.l2_gas_price.0);
            return self.l2_gas_price;
        };
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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L72-77)
```rust
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
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
