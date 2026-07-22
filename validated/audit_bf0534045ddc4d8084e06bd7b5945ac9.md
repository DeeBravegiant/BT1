### Title
Unconstrained `min_gas_price_percentage` in `StatefulTransactionValidatorConfig` Can Cause All Gateway Transactions to Be Rejected - (File: `crates/apollo_gateway_config/src/config.rs`)

### Summary
`StatefulTransactionValidatorConfig.min_gas_price_percentage` is a `u8` field (range 0–255) with no upper-bound validation annotation. When set above 100, the gateway computes a threshold that exceeds the actual network gas price, causing every incoming transaction to be rejected at the stateful validation layer. Unlike the analogous `fee_escalation_percentage` field in `MempoolStaticConfig`, which is correctly guarded with `#[validate(range(min = 1, max = 100))]`, `min_gas_price_percentage` carries no such constraint.

### Finding Description

`MempoolStaticConfig.fee_escalation_percentage` is correctly bounded: [1](#0-0) 

`StatefulTransactionValidatorConfig.min_gas_price_percentage` has no equivalent bound: [2](#0-1) 

The field is consumed in `validate_tx_l2_gas_price_within_threshold`: [3](#0-2) 

The threshold is computed as:
```
threshold = (min_gas_price_percentage / 100) * previous_block_l2_gas_price
```

When `min_gas_price_percentage = 200`, the threshold becomes `2 × previous_block_l2_gas_price`. Because transactions set `max_price_per_unit` at or near the current network gas price, every transaction will fail the check `tx_l2_gas_price.0 < threshold` and be rejected with `GAS_PRICE_TOO_LOW`.

The config test suite only validates the lower bound (zero) for `fee_escalation_percentage` and has no test for `min_gas_price_percentage > 100`: [4](#0-3) 

The production default is 100, but the schema exposes the field as a plain integer with no enforced ceiling: [5](#0-4) 

### Impact Explanation

**High. Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

Setting `min_gas_price_percentage` to any value above 100 causes `validate_tx_l2_gas_price_within_threshold` to compute a threshold that no normally-priced transaction can satisfy. The gateway will reject every `AllResources` transaction (V3 transactions) with a `GAS_PRICE_TOO_LOW` error. The sequencer continues running but accepts no user transactions, effectively halting block production.

### Likelihood Explanation

Low-to-medium. The value is operator-controlled via config file or dynamic config update. An operator could accidentally set it above 100 (e.g., intending "150% of minimum" as a stricter filter, or a copy-paste error). There is no runtime guard to catch the misconfiguration before it takes effect, unlike `fee_escalation_percentage` which would fail `validate()` at startup.

### Recommendation

Add a `#[validate(range(min = 0, max = 100))]` annotation to `min_gas_price_percentage` in `StatefulTransactionValidatorConfig`, mirroring the existing guard on `fee_escalation_percentage`:

```rust
#[validate(range(min = 0, max = 100))]
pub min_gas_price_percentage: u8,
```

Add a corresponding test in the gateway config test suite:

```rust
#[test]
fn min_gas_price_percentage_above_100_fails_validation() {
    let config = StatefulTransactionValidatorConfig {
        min_gas_price_percentage: 101,
        ..Default::default()
    };
    assert!(config.validate().is_err());
}
```

### Proof of Concept

1. Deploy the sequencer with `gateway_config.static_config.stateful_tx_validator_config.min_gas_price_percentage = 200` (or update via dynamic config).
2. Submit any V3 `invoke` transaction with `l2_gas.max_price_per_unit` equal to the current network L2 gas price.
3. `validate_tx_l2_gas_price_within_threshold` computes `threshold = 2 × current_gas_price`.
4. The check `tx_l2_gas_price.0 < threshold` is true for every normally-priced transaction.
5. All transactions are rejected with `StarknetErrorCode::GAS_PRICE_TOO_LOW`.
6. The mempool receives no transactions; block production stalls. [6](#0-5)

### Citations

**File:** crates/apollo_mempool_config/src/config.rs (L64-66)
```rust
    // Percentage increase for tip and max gas price to enable transaction replacement.
    #[validate(range(min = 1, max = 100))]
    pub fee_escalation_percentage: u8, // E.g., 10 for a 10% increase.
```

**File:** crates/apollo_gateway_config/src/config.rs (L276-287)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct StatefulTransactionValidatorConfig {
    // If true, ensures the max L2 gas price exceeds (a configurable percentage of) the base gas
    // price of the previous block.
    pub validate_resource_bounds: bool,
    pub max_allowed_nonce_gap: u32,
    pub reject_future_declare_txs: bool,
    pub max_nonce_for_validation_skip: Nonce,
    pub versioned_constants_overrides: Option<VersionedConstantsOverrides>,
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L358-390)
```rust
    // TODO(Arni): Consider running this validation for all gas prices.
    fn validate_tx_l2_gas_price_within_threshold(
        &self,
        tx_resource_bounds: ValidResourceBounds,
        previous_block_l2_gas_price: NonzeroGasPrice,
    ) -> StatefulTransactionValidatorResult<()> {
        match tx_resource_bounds {
            ValidResourceBounds::AllResources(tx_resource_bounds) => {
                let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
                    return Err(StarknetError {
                        // We didn't have this kind of an error.
                        code: StarknetErrorCode::UnknownErrorCode(
                            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
                        ),
                        message: format!(
                            "Transaction L2 gas price {tx_l2_gas_price} is below the required \
                             threshold {threshold}.",
                        ),
                    });
                }
            }
            ValidResourceBounds::L1Gas(_) => {
                // No validation required for legacy transactions.
            }
        }
        Ok(())
    }
```

**File:** crates/apollo_mempool_config/src/config_test.rs (L10-14)
```rust
#[test]
fn zero_fee_escalation_percentage_fails_validation() {
    let static_config = MempoolStaticConfig { fee_escalation_percentage: 0, ..Default::default() };
    assert!(static_config.validate().is_err());
}
```

**File:** crates/apollo_node/resources/config_schema.json (L3112-3116)
```json
  "gateway_config.static_config.stateful_tx_validator_config.min_gas_price_percentage": {
    "description": "Minimum gas price as percentage of threshold to accept transactions.",
    "privacy": "Public",
    "value": 100
  },
```
