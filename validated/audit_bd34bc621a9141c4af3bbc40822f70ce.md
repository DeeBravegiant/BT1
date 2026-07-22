### Title
Proposer-Controlled Timestamp Used as L1 Gas Price Reference Allows Manipulation of Block Gas Prices — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary
In `is_proposal_init_valid`, the validator computes its reference L1 gas prices by querying `get_l1_prices_in_fri_and_wei` with `init_proposed.timestamp` — a value fully controlled by the proposer. Because the same proposer-supplied timestamp drives both the proposer's price selection and the validator's reference computation, the proposer can choose a timestamp within the allowed window where L1 gas was cheaper (or more expensive), embed matching prices in `ProposalInit`, and have the validator accept them. The result is that the block's authoritative L1 gas prices — used for all transaction fee calculations — can be systematically biased away from the current market rate.

### Finding Description

In `is_proposal_init_valid` the validator calls `get_l1_prices_in_fri_and_wei` with the proposer-supplied `init_proposed.timestamp` to derive its reference L1 gas prices:

```rust
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,   // ← proposer-controlled
    proposal_init_validation.previous_proposal_init.as_ref(),
    gas_price_params,
)
.await;
``` [1](#0-0) 

It then checks that the proposer's stated prices are within a percentage margin of that reference:

```rust
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, ...)
    && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, ...)
    && within_margin(l1_data_gas_price_wei_proposed, l1_data_gas_price_wei, ...))
``` [2](#0-1) 

The timestamp window check only requires:

```rust
if init_proposed.timestamp < last_block_timestamp { ... }
if init_proposed.timestamp > now + proposal_init_validation.block_timestamp_window_seconds { ... }
``` [3](#0-2) 

The production value of `block_timestamp_window_seconds` is 1 second, but the **lower bound is only `>= last_block_timestamp`**. If the previous block was produced 60 seconds ago, the proposer has a 61-second window to choose from. [4](#0-3) 

Inside `get_price_info`, the timestamp is used to select which historical L1 blocks to average:

```rust
let index_last_timestamp_rev = samples.iter().rev().position(|data| {
    data.timestamp <= timestamp.saturating_sub(&self.config.lag_margin_seconds.as_secs())
});
``` [5](#0-4) 

Different timestamps yield different price averages — this is explicitly confirmed by the test `gas_price_provider_timestamp_changes_mean`. [6](#0-5) 

**Attack path:**

1. The proposer observes that L1 gas was cheap 40 seconds ago (e.g., a temporary L1 activity drop).
2. The last block was produced 41 seconds ago, so `init_proposed.timestamp = now − 40` is within the allowed window.
3. The proposer sets `l1_gas_price_fri` to match the cheap prices at `now − 40`.
4. The validator calls `get_l1_prices_in_fri_and_wei(now − 40)` and derives the same cheap reference.
5. `within_margin(cheap_proposed, cheap_reference, margin)` passes.
6. The proposal is accepted; all transactions in the block execute against the artificially low L1 gas prices.

The `within_margin` function is correctly anchored to the reference (not the proposed value), but the reference itself is derived from the proposer's timestamp, so anchoring provides no protection here. [7](#0-6) 

The accepted L1 gas prices flow directly into `BlockInfo` via `convert_to_sn_api_block_info` and are used by the batcher for all transaction fee calculations: [8](#0-7) 

### Impact Explanation

The L1 gas prices embedded in `ProposalInit` become the authoritative `BlockInfo.gas_prices` used by the blockifier to compute fees for every transaction in the block. A proposer who selects a timestamp where L1 gas was 20% cheaper can embed 20% lower L1 gas prices; the validator accepts them because its own reference is computed from the same timestamp. This constitutes an incorrect L1 gas price effect with direct economic impact: users underpay L1 gas fees, and the protocol under-collects revenue. The manipulation magnitude is bounded by how much L1 gas prices varied during the `[last_block_timestamp, now + 1]` window, which can be significant during slow block production periods or volatile L1 markets.

### Likelihood Explanation

The proposer is the only party who needs to act. No special network conditions are required beyond the last block being produced more than a few seconds ago (normal during any network slowdown). The proposer simply picks the cheapest timestamp within the allowed range, computes the matching prices from the same L1 gas price provider, and embeds them. No cryptographic material needs to be forged. The manipulation is repeatable every block.

### Recommendation

The validator should compute its reference L1 gas prices using the **local clock time** (`clock.unix_now()`), not `init_proposed.timestamp`. This decouples the reference from the proposer's input:

```rust
let reference_timestamp = clock.unix_now();  // not init_proposed.timestamp
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    reference_timestamp,
    ...
)
.await;
```

This mirrors the fix recommended in the external report: introduce a separate tracking mechanism that is not affected by the attacker-controlled update, and remove the attacker-controlled value from the critical check path.

### Proof of Concept

```
Precondition: last block produced T_prev = now − 60 seconds.
Allowed timestamp range: [now − 60, now + 1].

Step 1: Proposer queries get_price_info(now − 55) → PriceInfo { base_fee: 50 gwei, blob_fee: 1 gwei }
        (L1 was cheap 55 seconds ago)
Step 2: Proposer queries get_price_info(now)      → PriceInfo { base_fee: 80 gwei, blob_fee: 3 gwei }
        (current market rate)

Step 3: Proposer sets:
          init.timestamp         = now − 55
          init.l1_gas_price_fri  = 50 gwei  (matches reference at now − 55)
          init.l1_data_gas_price_fri = 1 gwei

Step 4: Validator calls get_l1_prices_in_fri_and_wei(init.timestamp = now − 55)
        → reference = { l1_gas: 50 gwei, l1_data_gas: 1 gwei }

Step 5: within_margin(50 gwei, 50 gwei, margin%) → true  ✓
        Proposal accepted.

Step 6: convert_to_sn_api_block_info(init) embeds l1_gas_price_fri = 50 gwei into BlockInfo.
        All transactions in the block pay fees at 50 gwei instead of 80 gwei.
        Result: 37.5% reduction in L1 gas fees collected.
```

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L263-285)
```rust
    if init_proposed.timestamp < last_block_timestamp {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is too old: last_block_timestamp={}, proposed={}",
                last_block_timestamp, init_proposed.timestamp
            ),
        ));
    }
    if init_proposed.timestamp > now + proposal_init_validation.block_timestamp_window_seconds {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is in the future: now={}, block_timestamp_window_seconds={}, \
                 proposed={}",
                now,
                proposal_init_validation.block_timestamp_window_seconds,
                init_proposed.timestamp
            ),
        ));
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L322-328)
```rust
    let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        proposal_init_validation.previous_proposal_init.as_ref(),
        gas_price_params,
    )
    .await;
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L342-368)
```rust
    if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_fri_proposed,
            l1_data_gas_price_fri,
            l1_gas_price_margin_percent,
        )
        && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_wei_proposed,
            l1_data_gas_price_wei,
            l1_gas_price_margin_percent,
        ))
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "L1 gas price mismatch: expected L1 gas price FRI={l1_gas_price_fri}, \
                 proposed={l1_gas_price_fri_proposed}, expected L1 data gas price \
                 FRI={l1_data_gas_price_fri}, proposed={l1_data_gas_price_fri_proposed}, expected \
                 L1 gas price WEI={l1_gas_price_wei}, proposed={l1_gas_price_wei_proposed}, \
                 expected L1 data gas price WEI={l1_data_gas_price_wei}, \
                 proposed={l1_data_gas_price_wei_proposed}, \
                 l1_gas_price_margin_percent={l1_gas_price_margin_percent}"
            ),
        ));
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L421-438)
```rust
/// Returns whether `proposed` is within `margin_percent` of the locally-trusted `reference`,
/// i.e. within the symmetric band `[reference*(1-m), reference*(1+m)]`.
///
/// The band is anchored to `reference` (the node's own L1 oracle read), not to the
/// proposer-supplied `proposed`: anchoring to `proposed` would let a malicious proposer scale the
/// band width with its own input and widen it in its favor.
fn within_margin(proposed: GasPrice, reference: GasPrice, margin_percent: u128) -> bool {
    // For small numbers (e.g., less than 10 wei, if margin is 10%), even an off-by-one
    // error might be bigger than the margin, even if it is just a rounding error.
    // We make an exception for such mismatch, and don't bother checking percentages
    // if the difference in price is only one wei.
    if proposed.0.abs_diff(reference.0) <= GAS_PRICE_ABS_DIFF_MARGIN {
        return true;
    }
    // Saturate: `reference.0 * margin_percent` can overflow u128 on large WEI prices.
    let margin = reference.0.saturating_mul(margin_percent) / 100;
    proposed.0.abs_diff(reference.0) <= margin
}
```

**File:** crates/apollo_node/resources/config_schema.json (L2787-2791)
```json
  "consensus_manager_config.context_config.static_config.block_timestamp_window_seconds": {
    "description": "Maximum allowed deviation (seconds) of a proposed block's timestamp from the current time.",
    "privacy": "Public",
    "value": 1
  },
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L144-147)
```rust
        // This index is for the last block in the mean (inclusive).
        let index_last_timestamp_rev = samples.iter().rev().position(|data| {
            data.timestamp <= timestamp.saturating_sub(&self.config.lag_margin_seconds.as_secs())
        });
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider_test.rs (L127-141)
```rust
#[test]
fn gas_price_provider_timestamp_changes_mean() {
    let (provider, _block_prices, timestamp3) = make_provider();
    let lag = provider.config.lag_margin_seconds.as_secs();

    // timestamp3 is used to define the interval of blocks 1 to 3.
    let PriceInfo { base_fee_per_gas: gas_price, blob_fee: data_gas_price } =
        provider.get_price_info(BlockTimestamp(timestamp3 + lag)).unwrap();

    // If we take a higher timestamp the gas prices should change.
    let PriceInfo { base_fee_per_gas: gas_price_new, blob_fee: data_gas_price_new } =
        provider.get_price_info(BlockTimestamp(timestamp3 + lag * 2)).unwrap();
    assert_ne!(gas_price_new, gas_price);
    assert_ne!(data_gas_price_new, data_gas_price);
}
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L301-347)
```rust
pub(crate) fn convert_to_sn_api_block_info(
    init: &ProposalInit,
) -> Result<starknet_api::block::BlockInfo, StarknetApiError> {
    if init.l1_gas_price_fri.0 == 0
        || init.l1_gas_price_wei.0 == 0
        || init.l1_data_gas_price_fri.0 == 0
        || init.l1_data_gas_price_wei.0 == 0
        || init.l2_gas_price_fri.0 == 0
    {
        warn!("Zero gas price detected in block info: {:?}", init);
    }

    let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
    let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
    let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
    let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
    let l2_gas_price_fri = NonzeroGasPrice::new(init.l2_gas_price_fri)?;
    let proposal_init_info = PreviousProposalInitInfo::from(init);
    let eth_to_fri_rate = calculate_eth_to_fri_rate(&proposal_init_info)?;

    let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
        .inspect_err(|_| {
            warn!(
                "L2 gas price in wei is zero! Conversion rate: {eth_to_fri_rate}, L2 gas price in \
                 FRI: {}",
                init.l2_gas_price_fri
            )
        })?;
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
        gas_prices: GasPrices {
            strk_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_fri,
                l1_data_gas_price: l1_data_gas_price_fri,
                l2_gas_price: l2_gas_price_fri,
            },
            eth_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_wei,
                l1_data_gas_price: l1_data_gas_price_wei,
                l2_gas_price: l2_gas_price_wei,
            },
        },
        use_kzg_da: init.l1_da_mode.is_use_kzg_da(),
        starknet_version: init.starknet_version,
    })
```
