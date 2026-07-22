### Title
Validator Uses Proposer-Supplied Timestamp to Query L1 Gas Price Reference, Enabling Stale Price Acceptance — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

In `is_proposal_init_valid`, the validator fetches its own L1 gas price reference using `init_proposed.timestamp` — a value fully controlled by the proposer — rather than the validator's own current clock time. Because the timestamp lower-bound check only requires `init_proposed.timestamp >= last_block_timestamp` (no `now - X` floor), a proposer can set the timestamp to the previous block's timestamp, which may be arbitrarily far in the past during slow block production or after a chain halt. The validator then queries the L1 gas price provider for that past timestamp, obtains stale prices, and accepts the proposal's L1 gas prices as valid against that stale reference. This is the direct sequencer analog of the Ditto M-07 bug: a cached/stale price is used as the reference for a critical economic decision instead of the current price.

---

### Finding Description

In `is_proposal_init_valid` the validator calls:

```rust
// validate_proposal.rs line 322-328
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,          // ← proposer-controlled value
    proposal_init_validation.previous_proposal_init.as_ref(),
    gas_price_params,
)
.await;
``` [1](#0-0) 

The timestamp passed to `get_l1_prices_in_fri_and_wei` is `init_proposed.timestamp`, not `clock.unix_now()`. The timestamp validation that precedes this call only enforces:

```rust
// validate_proposal.rs lines 263-284
if init_proposed.timestamp < last_block_timestamp { ... }   // lower bound = previous block
if init_proposed.timestamp > now + block_timestamp_window_seconds { ... }  // upper bound
``` [2](#0-1) 

There is **no lower bound of the form `now - X`**. The lower bound is `last_block_timestamp`, the timestamp of the most recently committed block. During slow block production or after a chain halt, `last_block_timestamp` can be many minutes or hours behind `now`. A proposer can therefore legally set `init_proposed.timestamp = last_block_timestamp` (or any value up to `now + block_timestamp_window_seconds`), causing the validator to query L1 prices for a potentially stale point in time.

Inside `get_l1_prices_in_fri_and_wei` → `get_l1_prices_in_fri_and_wei_and_conversion_rate`, the L1 gas price provider is queried with that past timestamp:

```rust
// utils.rs lines 148-151
let (eth_to_fri_rate, price_info) = tokio::join!(
    l1_gas_price_provider_client.get_rate(timestamp),
    l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
);
``` [3](#0-2) 

`get_price_info` returns the sliding-window mean of L1 blocks whose timestamps are ≤ `timestamp − lag_margin_seconds`. If the proposer sets `timestamp` to a point in the past when L1 gas prices were lower, the validator's reference price is that lower historical mean. The proposer then includes those same lower prices in `init_proposed.l1_gas_price_fri` / `l1_gas_price_wei`, and the `within_margin` check passes because both sides are anchored to the same stale value. [4](#0-3) 

The `within_margin` guard is anchored to the validator's own reference, not the proposer's value — which is correct — but the reference itself is derived from the proposer-supplied timestamp:

```rust
// validate_proposal.rs lines 342-353
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, ...)
  && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, ...)
  && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, ...)
  && within_margin(l1_data_gas_price_wei_proposed, l1_data_gas_price_wei, ...))
``` [5](#0-4) 

Because the reference is computed from the proposer-supplied timestamp, the proposer can choose a timestamp that maps to a favorable (stale, lower) reference and then propose prices that match it.

A second path exists via the staleness fallback. If the proposer sets `init_proposed.timestamp` to a value where `timestamp > last_l1_block_timestamp + max_time_gap_seconds`, `get_price_info` returns `StaleL1GasPricesError`:

```rust
// l1_gas_price_provider.rs lines 136-142
if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
    return Err(L1GasPriceProviderError::StaleL1GasPricesError { ... });
}
``` [6](#0-5) 

`get_l1_prices_in_fri_and_wei_and_conversion_rate` then silently falls back to `previous_proposal_init` prices — the prices from the last committed block:

```rust
// utils.rs lines 186-199
if let Some(prev_info) = previous_proposal_init {
    ...
    return (prev_l1_gas_price, prev_l1_gas_price_wei, eth_to_fri_rate);
}
``` [7](#0-6) 

The proposer, knowing this fallback will trigger, can include the previous block's L1 prices in the new proposal. The validator accepts them because its own reference is also the previous block's prices.

---

### Impact Explanation

The accepted block's `BlockInfo` is constructed directly from `init_proposed` fields:

```rust
// utils.rs lines 329-347
Ok(starknet_api::block::BlockInfo {
    gas_prices: GasPrices {
        strk_gas_prices: GasPriceVector {
            l1_gas_price: l1_gas_price_fri,
            l1_data_gas_price: l1_data_gas_price_fri,
            ...
        },
        ...
    },
    ...
})
``` [8](#0-7) 

Every transaction executed in that block uses these gas prices for fee calculation. Stale, artificially low L1 gas prices cause users to underpay for L1 data availability costs, creating an economic loss for the protocol. This matches the **"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact"** impact category.

---

### Likelihood Explanation

The attack is most effective during:
1. **Slow block production** — `last_block_timestamp` drifts far behind `now`, widening the proposer's timestamp choice window.
2. **High L1 gas price volatility** — the difference between current and past L1 prices is large, making the stale reference materially wrong.
3. **L1 scraper lag** — if the scraper falls behind, a future timestamp triggers the staleness fallback, forcing the validator to use previous-block prices as the reference.

These conditions coincide with periods of network stress, exactly when accurate L1 gas prices matter most (analogous to the Ditto report's "high volatility" scenario). The proposer role in Tendermint rotates, so any validator node that becomes proposer can trigger this.

---

### Recommendation

Replace `init_proposed.timestamp` with `clock.unix_now()` when querying the validator's own L1 gas price reference in `is_proposal_init_valid`:

```rust
// validate_proposal.rs — is_proposal_init_valid
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
-   init_proposed.timestamp,
+   clock.unix_now(),          // validator's own current time, not proposer-supplied
    proposal_init_validation.previous_proposal_init.as_ref(),
    gas_price_params,
)
.await;
```

This mirrors how liquidations in Ditto update the oracle before processing, ensuring the reference price reflects the current market rather than a proposer-chosen historical point. The proposer's timestamp is still validated for block ordering purposes; it simply should not control which L1 prices the validator uses as its reference.

Additionally, add a lower-bound timestamp check against `now - max_allowed_past_seconds` to prevent proposals with timestamps far in the past from being accepted at all.

---

### Proof of Concept

1. L1 gas price at `now` = 100 gwei. L1 gas price 5 minutes ago = 60 gwei.
2. The previous Starknet block was produced 5 minutes ago (`last_block_timestamp = now - 300`).
3. Proposer constructs `ProposalInit` with:
   - `timestamp = last_block_timestamp` (= `now - 300`, passes the `>= last_block_timestamp` check)
   - `l1_gas_price_wei = 60 gwei` (the price 5 minutes ago)
4. Validator receives the proposal and calls `is_proposal_init_valid`.
5. Validator calls `get_l1_prices_in_fri_and_wei(timestamp = now - 300, ...)`.
6. `get_price_info(BlockTimestamp(now - 300))` returns the mean over L1 blocks up to `now - 300 - lag` → returns ~60 gwei.
7. `within_margin(60 gwei, 60 gwei, margin%)` → `true`.
8. Proposal is accepted. The committed block's `BlockInfo` carries `l1_gas_price = 60 gwei`.
9. All transactions in the block pay fees based on 60 gwei L1 gas price, while the actual L1 cost is 100 gwei. The protocol absorbs the 40 gwei difference per unit of L1 gas consumed. [9](#0-8) [10](#0-9)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L252-328)
```rust
#[instrument(level = "warn", skip_all, fields(?proposal_init_validation, ?init_proposed))]
async fn is_proposal_init_valid(
    proposal_init_validation: &ProposalInitValidation,
    init_proposed: &ProposalInit,
    clock: &dyn Clock,
    l1_gas_price_provider: Arc<dyn L1GasPriceProviderClient>,
    gas_price_params: &GasPriceParams,
) -> ValidateProposalResult<()> {
    let now: u64 = clock.unix_now();
    let last_block_timestamp =
        proposal_init_validation.previous_proposal_init.as_ref().map_or(0, |info| info.timestamp);
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
    if init_proposed.starknet_version != proposal_init_validation.starknet_version {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "starknet_version mismatch: expected={:?}, proposed={:?}",
                proposal_init_validation.starknet_version, init_proposed.starknet_version
            ),
        ));
    }
    // `version_constant_commitment` is proposer-supplied (network-derived). It is not yet a real
    // commitment (see `expected_version_constant_commitment`): the only valid value is the
    // sentinel, so reject anything else. Enforcing the same value the proposer emits keeps the two
    // sides in lockstep, so a real value cannot ship on one side without the other.
    let expected_commitment = expected_version_constant_commitment();
    if init_proposed.version_constant_commitment != expected_commitment {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "version_constant_commitment mismatch: expected={expected_commitment:?}, \
                 proposed={:?}",
                init_proposed.version_constant_commitment
            ),
        ));
    }
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
    let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        proposal_init_validation.previous_proposal_init.as_ref(),
        gas_price_params,
    )
    .await;
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L342-353)
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
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L136-182)
```rust
pub(crate) async fn get_l1_prices_in_fri_and_wei_and_conversion_rate(
    l1_gas_price_provider_client: Arc<dyn L1GasPriceProviderClient>,
    timestamp: u64,
    previous_proposal_init: Option<&PreviousProposalInitInfo>,
    gas_price_params: &GasPriceParams,
) -> (L1PricesInFri, L1PricesInWei, u128) {
    // One of these paths should fill the return values:
    // 1. Both L1 gas price and eth/strk rate are Ok, use those.
    // 2. Otherwise, use previous block info.
    // 3. If that isn't available either, use min gas prices and default eth/strk rate.

    // Get the eth to fri rate from the oracle, and the L1 gas price (in wei) from the provider.
    let (eth_to_fri_rate, price_info) = tokio::join!(
        l1_gas_price_provider_client.get_rate(timestamp),
        l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
    );
    if price_info.is_err() {
        warn!("Failed to get l1 gas price from provider: {:?}", price_info);
        CONSENSUS_L1_GAS_PRICE_PROVIDER_ERROR.increment(1);
    }
    if eth_to_fri_rate.is_err() {
        warn!("Failed to get eth to fri rate from oracle: {:?}", eth_to_fri_rate);
    }
    if let (Ok(eth_to_fri_rate), Ok(mut price_info)) = (eth_to_fri_rate, price_info) {
        // Both L1 prices and rate are Ok, so we can use them.
        info!(
            "raw eth_to_fri_rate (from oracle): {eth_to_fri_rate}, raw l1 gas price wei (from \
             provider): {price_info:?}"
        );
        apply_fee_transformations(&mut price_info, gas_price_params);
        let prices_in_wei = L1PricesInWei {
            l1_gas_price: price_info.base_fee_per_gas,
            l1_data_gas_price: price_info.blob_fee,
        };
        // Apply the eth/strk rate to get prices in fri.
        let l1_gas_prices_fri_result =
            L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
        // If conversion fails, leave return_value=None to try backup methods.
        if let Ok(prices_in_fri) = l1_gas_prices_fri_result {
            return (prices_in_fri, prices_in_wei, eth_to_fri_rate);
        } else {
            warn!(
                "Failed to convert L1 gas prices to FRI: {:?}",
                l1_gas_prices_fri_result.clone().err()
            );
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L186-199)
```rust
    if let Some(prev_info) = previous_proposal_init {
        let prev_l1_gas_price_wei = prev_info.l1_prices_wei.clone();
        let prev_l1_gas_price = prev_info.l1_prices_fri.clone();
        // This calculation can fail if gas price is too high, or zero, or if the prices cause the
        // rate to be zero.
        let eth_to_fri_rate = calculate_eth_to_fri_rate(prev_info);
        match eth_to_fri_rate {
            Ok(eth_to_fri_rate) => {
                info!(
                    "Using previous block info: wei prices: {:?}, fri prices: {:?}, eth to fri \
                     rate: {:?}",
                    prev_l1_gas_price_wei, prev_l1_gas_price, eth_to_fri_rate
                );
                return (prev_l1_gas_price, prev_l1_gas_price_wei, eth_to_fri_rate);
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L329-347)
```rust
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

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L136-142)
```rust
        // Check if the prices are stale.
        if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
            return Err(L1GasPriceProviderError::StaleL1GasPricesError {
                current_timestamp: timestamp.0,
                last_valid_price_timestamp: *last_timestamp,
            });
        }
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L144-160)
```rust
        // This index is for the last block in the mean (inclusive).
        let index_last_timestamp_rev = samples.iter().rev().position(|data| {
            data.timestamp <= timestamp.saturating_sub(&self.config.lag_margin_seconds.as_secs())
        });

        // Could not find a block with the requested timestamp and lag.
        let Some(last_index_rev) = index_last_timestamp_rev else {
            return Err(L1GasPriceProviderError::MissingDataError {
                timestamp: timestamp.0,
                lag: self.config.lag_margin_seconds.as_secs(),
            });
        };
        // Convert the index to the forward direction.
        // `last_index` should be one past the final entry we will include in our calculation.
        // The index returned from `position` is guaranteed to be less than `len()`,
        // so `last_index` is guaranteed to be >= 1.
        let last_index = samples.len() - last_index_rev;
```
