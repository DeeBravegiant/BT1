### Title
Inconsistent `l1_gas_price_fri`/`l1_gas_price_wei` Ratio in `ProposalInit` Allows Malicious Proposer to Distort `l2_gas_price_wei` and ETH-Denominated L2 Gas Fees — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_proposal_init_valid` validates `l1_gas_price_fri` and `l1_gas_price_wei` **independently** against the validator's oracle, but never validates their **mutual consistency** (the implied ETH/STRK conversion rate). A malicious proposer can set both values within the allowed 10% margin while placing them at opposite ends of that margin, distorting the implied rate by up to ~22%. `convert_to_sn_api_block_info` then derives `l2_gas_price_wei` from this distorted rate, causing every ETH-denominated L2 gas fee in the block to be wrong by up to ~22%.

---

### Finding Description

**Independent validation without cross-consistency check.**

In `is_proposal_init_valid`, the four L1 price fields are checked one-by-one against the validator's own oracle-derived reference:

```rust
// validate_proposal.rs:342-353
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, l1_gas_price_margin_percent)
    && within_margin(l1_data_gas_price_wei_proposed, l1_data_gas_price_wei, l1_gas_price_margin_percent))
``` [1](#0-0) 

No check is performed on the ratio `l1_gas_price_fri_proposed / l1_gas_price_wei_proposed` (the implied ETH/STRK rate).

**Distorted rate propagates into `l2_gas_price_wei`.**

`convert_to_sn_api_block_info` derives `eth_to_fri_rate` directly from the proposer-supplied prices:

```rust
// utils.rs:318-328
let proposal_init_info = PreviousProposalInitInfo::from(init);
let eth_to_fri_rate = calculate_eth_to_fri_rate(&proposal_init_info)?;
let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
``` [2](#0-1) 

`calculate_eth_to_fri_rate` computes:

```
eth_to_fri_rate = l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei
``` [3](#0-2) 

If the proposer sets `l1_gas_price_fri` at +10% and `l1_gas_price_wei` at −10% (both pass `within_margin`), the implied rate becomes `actual_rate × (1.1 / 0.9) ≈ actual_rate × 1.222`. The resulting `l2_gas_price_wei` is then `correct_l2_gas_price_wei / 1.222 ≈ correct × 0.818` — **18% below the correct value**.

**The distorted value is committed to the block hash.**

`l2_gas_price_wei` is placed into `eth_gas_prices.l2_gas_price` inside the `BlockInfo` passed to the blockifier:

```rust
// utils.rs:339-343
eth_gas_prices: GasPriceVector {
    l1_gas_price: l1_gas_price_wei,
    l1_data_gas_price: l1_data_gas_price_wei,
    l2_gas_price: l2_gas_price_wei,
},
``` [4](#0-3) 

This `BlockInfo` feeds `PartialBlockHashComponents::new`, which commits `l2_gas_price` (both FRI and WEI) into the block hash:

```rust
// block_hash_calculator.rs:224-235
pub fn new(block_info: &BlockInfo, header_commitments: BlockHeaderCommitments) -> Self {
    Self {
        ...
        l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
        ...
    }
}
``` [5](#0-4) 

The validator's `l2_gas_price_fri` is validated exactly (must equal the configured value), but `l2_gas_price_wei` is never validated — it is silently derived from the distorted ratio and committed permanently.

---

### Impact Explanation

**Impact: Critical — Incorrect fee with economic impact.**

Every transaction in the block that pays L2 gas in ETH uses `eth_gas_prices.l2_gas_price` for fee calculation. With a ~22% distortion in the implied rate, users can be overcharged or undercharged by up to ~22% on ETH-denominated L2 gas fees. The wrong value is committed to the block hash and stored permanently. The proposer can repeat this on every block they lead.

---

### Likelihood Explanation

Any consensus validator (achievable permissionlessly by staking) can craft a `ProposalInit` with `l1_gas_price_fri` and `l1_gas_price_wei` at opposite ends of the 10% margin. No special access beyond validator status is required. The attack is silent — both individual margin checks pass, and no error is logged.

---

### Recommendation

After the individual margin checks pass, add a cross-consistency check: verify that the implied ETH/STRK rate derived from the proposer's prices (`l1_gas_price_fri_proposed / l1_gas_price_wei_proposed`) is within the allowed margin of the validator's own oracle-derived rate. For example:

```rust
let proposed_rate = l1_gas_price_fri_proposed.0
    .checked_mul(WEI_PER_ETH)
    .and_then(|v| v.checked_div(l1_gas_price_wei_proposed.0));
let reference_rate = l1_gas_price_fri.0
    .checked_mul(WEI_PER_ETH)
    .and_then(|v| v.checked_div(l1_gas_price_wei.0));
// Reject if proposed_rate is outside margin of reference_rate
```

This closes the gap between the two independently-validated prices and prevents the implied rate from being distorted beyond the intended tolerance.

---

### Proof of Concept

1. Validator oracle returns: `l1_gas_price_wei = 100 gwei`, `eth_to_fri_rate = 1000`, so `l1_gas_price_fri = 100,000 fri`. Margin = 10%.
2. Malicious proposer sets: `l1_gas_price_fri = 110,000 fri` (+10%, passes `within_margin`), `l1_gas_price_wei = 90 gwei` (−10%, passes `within_margin`).
3. Both individual checks pass in `is_proposal_init_valid`.
4. `convert_to_sn_api_block_info` computes: `eth_to_fri_rate = 110,000 × 10^18 / 90×10^9 ≈ 1,222` (22% above actual 1,000).
5. `l2_gas_price_fri = 1,000,000 fri` (validated exactly, correct).
6. `l2_gas_price_wei = 1,000,000 × 10^18 / 1,222 ≈ 818 gwei` (should be `1,000 gwei`; 18% below correct).
7. All transactions in the block pay 18% less ETH for L2 gas than they should; the wrong value is committed to the block hash via `PartialBlockHashComponents`.

### Citations

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L318-328)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L339-343)
```rust
            eth_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_wei,
                l1_data_gas_price: l1_data_gas_price_wei,
                l2_gas_price: l2_gas_price_wei,
            },
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L503-531)
```rust
fn calculate_eth_to_fri_rate(
    proposal_init_info: &PreviousProposalInitInfo,
) -> Result<u128, StarknetApiError> {
    let eth_to_fri_rate = proposal_init_info
        .l1_prices_fri
        .l1_gas_price
        .0
        .checked_mul(WEI_PER_ETH)
        .ok_or_else(|| {
            StarknetApiError::GasPriceConversionError(format!(
                "Gas price in Fri should be small enough to multiply by WEI_PER_ETH. Previous \
                 proposal init info: {:?}",
                proposal_init_info
            ))
        })?
        .checked_div(proposal_init_info.l1_prices_wei.l1_gas_price.0)
        .ok_or_else(|| {
            StarknetApiError::GasPriceConversionError(format!(
                "Gas price in Wei should be non-zero. Previous proposal init info: {:?}",
                proposal_init_info
            ))
        })?;
    if eth_to_fri_rate == 0 {
        return Err(StarknetApiError::GasPriceConversionError(format!(
            "Eth to fri rate is zero. Previous proposal init info: {:?}",
            proposal_init_info
        )));
    }
    Ok(eth_to_fri_rate)
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L223-235)
```rust
impl PartialBlockHashComponents {
    pub fn new(block_info: &BlockInfo, header_commitments: BlockHeaderCommitments) -> Self {
        Self {
            header_commitments,
            block_number: block_info.block_number,
            l1_gas_price: block_info.gas_prices.l1_gas_price_per_token(),
            l1_data_gas_price: block_info.gas_prices.l1_data_gas_price_per_token(),
            l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
            sequencer: SequencerContractAddress(block_info.sequencer_address),
            timestamp: block_info.block_timestamp,
            starknet_version: block_info.starknet_version,
        }
    }
```
