### Title
Gateway Admission Checks Only L2 Gas Price, Silently Admitting V3 Transactions with Zero L1/L1-Data Gas Price That Will Always Fail Execution — (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's stateful validator enforces a minimum-price threshold only on the **L2 gas** component of `AllResourceBounds`. The L1 gas price and L1 data gas price components are never checked against the current block's prices. A V3 transaction carrying `l1_gas.max_price_per_unit = 0` (or any value below the block's L1 gas price) passes every gateway check and is admitted to the mempool, yet is guaranteed to be rejected by the blockifier's `check_fee_bounds` during sequencing. This is the direct sequencer analog of the zkevm-rom BYTE bug: a multi-component value (`AllResourceBounds`) is range-checked on only one component (`l2_gas`) while the remaining components (`l1_gas`, `l1_data_gas`) are silently ignored.

---

### Finding Description

`StatefulTransactionValidator::validate_resource_bounds` reads only the previous block's **STRK L2 gas price** and delegates to `validate_tx_l2_gas_price_within_threshold`: [1](#0-0) 

Inside that function the match arm for `AllResources` extracts and checks **only** `l2_gas.max_price_per_unit`: [2](#0-1) 

The developer-acknowledged TODO on line 358 reads:
> `// TODO(Arni): Consider running this validation for all gas prices.`

The stateless validator's `validate_resource_bounds` similarly inspects only `l2_gas`: [3](#0-2) 

The only cross-component check in the stateless path is that `max_possible_fee(Tip::ZERO) != 0`, which is satisfied as long as **any** resource contributes a non-zero product — so a transaction with `l1_gas.max_price_per_unit = 0`, `l2_gas.max_price_per_unit ≥ threshold`, and `l2_gas.max_amount > 0` passes both validators.

In contrast, the blockifier's `check_fee_bounds` (called during `perform_pre_validation_stage`) validates **all three** resources against the block's actual prices: [4](#0-3) 

The production default configuration has `validate_resource_bounds: true` and `min_gas_price_percentage: 100`: [5](#0-4) 

---

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions before sequencing.**

Any unprivileged user can craft a V3 `InvokeV3` / `DeclareV3` / `DeployAccountV3` transaction with:
- `l1_gas.max_price_per_unit = 0` (or any value below the block's L1 STRK price)
- `l2_gas.max_price_per_unit ≥ threshold` (to pass the only enforced check)
- `l2_gas.max_amount > 0` (to satisfy the non-zero fee check)

Such a transaction passes both the stateless and stateful gateway validators, is forwarded to the mempool, and is selected for sequencing. The blockifier then rejects it with `TransactionFeeError::InsufficientResourceBounds { MaxGasPriceTooLow { resource: L1Gas } }` during `perform_pre_validation_stage` — before any state change — but only after consuming mempool slots and batcher execution budget. At high submission rates this constitutes a low-cost DoS vector against the admission pipeline.

---

### Likelihood Explanation

**High.** The attack requires no privileged access, no special account, and no on-chain funds (the transaction is rejected before fee deduction). The crafted transaction is structurally valid and indistinguishable from a legitimate transaction at the gateway layer. The incomplete check is explicitly flagged with a TODO comment, confirming it is a known gap rather than an intentional design choice.

---

### Recommendation

Extend `validate_tx_l2_gas_price_within_threshold` (or replace it with a unified function) to fetch all three STRK gas prices from the previous block header and apply the same percentage-threshold check to `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit`. The stateless validator's `validate_resource_bounds` should similarly enforce a non-zero minimum on all three price fields, mirroring the three-resource loop already present in the blockifier's `check_fee_bounds`.

---

### Proof of Concept

1. Construct a V3 invoke transaction with:
   ```
   resource_bounds = AllResourceBounds {
       l1_gas:      { max_amount: 1,   max_price_per_unit: 0 },   // zero L1 price
       l2_gas:      { max_amount: 100, max_price_per_unit: P },   // P ≥ threshold
       l1_data_gas: { max_amount: 1,   max_price_per_unit: 0 },   // zero data price
   }
   ```
2. Submit via the HTTP gateway (`starknet_addInvokeTransaction`).
3. **Stateless check** (`validate_resource_bounds`): `max_possible_fee = 100 * P > 0` ✓; `l2_gas.max_price_per_unit = P ≥ min_gas_price` ✓ — **passes**.
4. **Stateful check** (`validate_tx_l2_gas_price_within_threshold`): only `l2_gas.max_price_per_unit` is compared against `previous_block_l2_gas_price`; L1 prices are never read — **passes**.
5. Transaction enters the mempool and is forwarded to the batcher.
6. Blockifier calls `check_fee_bounds` → `l1_gas_resource_bounds.max_price_per_unit (0) < block.l1_gas_price (non-zero)` → `InsufficientResourceBounds { MaxGasPriceTooLow { resource: L1Gas } }` — **rejected**.
7. Transaction is dropped without state change, but gateway and mempool resources were consumed.

Repeat at high frequency to exhaust mempool capacity or batcher execution budget with zero on-chain cost.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
        // Skip this validation during the systems bootstrap phase.
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
        }
        Ok(())
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L398-458)
```rust
                    ValidResourceBounds::AllResources(AllResourceBounds {
                        l1_gas: l1_gas_resource_bounds,
                        l2_gas: l2_gas_resource_bounds,
                        l1_data_gas: l1_data_gas_resource_bounds,
                    }) => {
                        let GasPriceVector { l1_gas_price, l1_data_gas_price, l2_gas_price } =
                            block_info.gas_prices.gas_price_vector(fee_type);
                        vec![
                            (
                                L1Gas,
                                l1_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_gas,
                                *l1_gas_price,
                            ),
                            (
                                L1DataGas,
                                l1_data_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_data_gas,
                                *l1_data_gas_price,
                            ),
                            (
                                L2Gas,
                                l2_gas_resource_bounds,
                                minimal_gas_amount_vector.l2_gas,
                                *l2_gas_price,
                            ),
                        ]
                    }
                };
                let insufficiencies = resources_amount_tuple
                    .iter()
                    .flat_map(
                        |(resource, resource_bounds, minimal_gas_amount, actual_gas_price)| {
                            let mut insufficiencies_resource = vec![];
                            if minimal_gas_amount > &resource_bounds.max_amount {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasAmountTooLow {
                                        resource: *resource,
                                        max_gas_amount: resource_bounds.max_amount,
                                        minimal_gas_amount: *minimal_gas_amount,
                                    },
                                );
                            }
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
                            insufficiencies_resource
                        },
                    )
                    .collect::<Vec<_>>();
                if !insufficiencies.is_empty() {
                    return Err(Box::new(TransactionFeeError::InsufficientResourceBounds {
                        errors: insufficiencies,
                    }))?;
                }
```

**File:** crates/apollo_gateway_config/src/config.rs (L289-300)
```rust
impl Default for StatefulTransactionValidatorConfig {
    fn default() -> Self {
        StatefulTransactionValidatorConfig {
            validate_resource_bounds: true,
            max_allowed_nonce_gap: 200,
            reject_future_declare_txs: true,
            max_nonce_for_validation_skip: Nonce(Felt::ONE),
            min_gas_price_percentage: 100,
            versioned_constants_overrides: None,
        }
    }
}
```
