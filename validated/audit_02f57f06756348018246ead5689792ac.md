### Title
L2 Gas Consumed Omitted from RPC `FeeEstimation` Response, Producing Authoritative-Looking Wrong `overall_fee` Formula — (File: `crates/apollo_rpc_execution/src/objects.rs`)

### Summary

The `FeeEstimation` struct returned by `starknet_estimateFee` and `starknet_simulateTransactions` exposes `l2_gas_price` but omits `l2_gas_consumed`. Its `overall_fee` field is documented as equalling `gas_consumed * gas_price + data_gas_consumed * data_gas_price`, which is incorrect for V3 transactions that consume L2 gas. Any client that reconstructs or verifies the fee using the documented formula will compute a value lower than the actual fee charged, and any client that sets `l2_gas` resource bounds from the response components will set them to zero, causing the transaction to be rejected at execution.

### Finding Description

In `crates/apollo_rpc_execution/src/objects.rs`, the `FeeEstimation` struct is defined as:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,          // l1_gas
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,     // l1_data_gas
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    pub l2_gas_price: GasPrice,      // price present, consumed amount absent
    /// The total amount of fee. This is equal to:
    /// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
``` [1](#0-0) 

The builder function `tx_execution_output_to_fee_estimation` constructs the response:

```rust
let gas_vector = tx_execution_output.execution_info.receipt.gas;

Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
``` [2](#0-1) 

`gas_vector.l2_gas` is read from the receipt but never placed in the response. The `overall_fee` is taken from `receipt.fee`, which is the actual charged amount and does include L2 gas cost. However, the documented formula `gas_consumed * gas_price + data_gas_consumed * data_gas_price` omits the L2 gas term entirely.

For V3 (`AllResources`) transactions, the actual fee is:

```
fee = l1_gas * l1_gas_price
    + l1_data_gas * l1_data_gas_price
    + l2_gas * (l2_gas_price + tip)
``` [3](#0-2) 

The L2 gas component is non-trivial in practice. The prover test fixture records `l2_gas_consumed: "0xb56b6"` with `l2_gas_price: "0x1dcd65000"` and `overall_fee: "0x151eb86f3ed400"`, where the L2 gas term alone accounts for the dominant share of the fee. [4](#0-3) 

The OpenRPC schema for `FEE_ESTIMATE` also perpetuates the wrong formula in its `overall_fee` description field, confirming this is a systemic gap rather than a local comment error. [5](#0-4) 

### Impact Explanation

This matches the allowed impact: **"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

Two concrete failure modes:

1. **Wrong fee verification.** A client that checks `overall_fee == gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` will observe a mismatch for every V3 transaction that consumes L2 gas. The response is self-contradictory: the scalar `overall_fee` is correct, but the components provided cannot reproduce it.

2. **Wrong resource-bound setting.** A client that derives its `l2_gas` resource bound from the fee estimation response has no `l2_gas_consumed` field to read. The only rational choice is zero, which causes the transaction to fail the `check_resources_within_bounds` check at execution time, since the actual L2 gas consumed will exceed the zero bound. [6](#0-5) 

### Likelihood Explanation

Every V3 (`AllResources`) transaction submitted to `starknet_estimateFee` or `starknet_simulateTransactions` triggers this path. V3 is the current standard transaction version on Starknet. Any SDK or wallet that reconstructs the fee from the response components, or that reads `l2_gas_consumed` to set resource bounds, is affected without any special attacker action.

### Recommendation

1. Add `pub l2_gas_consumed: Felt` to `FeeEstimation` and populate it with `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Correct the `overall_fee` doc comment to: `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price + l2_gas_consumed * (l2_gas_price + tip)`.
3. Update the `FEE_ESTIMATE` schema in `starknet_api_openrpc.json` to add `l2_gas_consumed` as a required field and fix the formula description.

### Proof of Concept

```
1. Submit a V3 invoke transaction to starknet_estimateFee.
2. Receive FeeEstimation { gas_consumed: G1, data_gas_consumed: G2,
       l1_gas_price: P1, l1_data_gas_price: P2, l2_gas_price: P3,
       overall_fee: F }.
3. Compute formula_fee = G1 * P1 + G2 * P2.
4. Observe formula_fee < F  (L2 gas term is missing).
5. Set l2_gas resource bound = 0 (no l2_gas_consumed field available).
6. Submit the transaction with those bounds.
7. Transaction fails: actual l2_gas consumed > 0 > bound.
```

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L94-113)
```rust
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct FeeEstimation {
    /// Gas consumed by this transaction. This includes gas for DA in calldata mode.
    pub gas_consumed: Felt,
    /// The gas price for execution and calldata DA.
    pub l1_gas_price: GasPrice,
    /// Gas consumed by DA in blob mode.
    pub data_gas_consumed: Felt,
    /// The gas price for DA blob.
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    /// The L2 gas price for execution.
    pub l2_gas_price: GasPrice,
    /// The total amount of fee. This is equal to:
    /// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
    pub overall_fee: Fee,
    /// The unit in which the fee was paid (Wei/Fri).
    pub unit: PriceUnit,
}
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L161-183)
```rust
pub(crate) fn tx_execution_output_to_fee_estimation(
    tx_execution_output: &TransactionExecutionOutput,
    block_context: &BlockContext,
) -> ExecutionResult<FeeEstimation> {
    let gas_prices = &block_context.block_info().gas_prices;
    let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
        gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
    );

    let gas_vector = tx_execution_output.execution_info.receipt.gas;

    Ok(FeeEstimation {
        gas_consumed: gas_vector.l1_gas.0.into(),
        l1_gas_price,
        data_gas_consumed: gas_vector.l1_data_gas.0.into(),
        l1_data_gas_price,
        l2_gas_price,
        overall_fee: tx_execution_output.execution_info.receipt.fee,
        unit: tx_execution_output.price_unit,
    })
}
```

**File:** crates/starknet_api/src/execution_resources.rs (L156-186)
```rust
    pub fn cost(&self, gas_prices: &GasPriceVector, tip: Tip) -> Fee {
        let tipped_l2_gas_price =
            gas_prices.l2_gas_price.checked_add(tip.into()).unwrap_or_else(|| {
                panic!(
                    "Tip overflowed: addition of L2 gas price ({}) and tip ({}) resulted in \
                     overflow.",
                    gas_prices.l2_gas_price, tip
                )
            });

        let mut sum = Fee(0);
        for (gas, price, resource) in [
            (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
            (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
            (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
        ] {
            let cost = gas.checked_mul(price.get()).unwrap_or_else(|| {
                panic!(
                    "{resource} cost overflowed: multiplication of gas amount ({gas}) by price \
                     per unit ({price}) resulted in overflow."
                )
            });
            sum = sum.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Total cost overflowed: addition of current sum ({sum}) and cost of \
                     {resource} ({cost}) resulted in overflow."
                )
            });
        }
        sum
    }
```

**File:** crates/starknet_transaction_prover/resources/rpc_records/test_execute_with_prefetch.json (L134-143)
```json
              "fee_estimation": {
                "l1_data_gas_consumed": "0x80",
                "l1_data_gas_price": "0x3e8",
                "l1_gas_consumed": "0x0",
                "l1_gas_price": "0xe8d4a51000",
                "l2_gas_consumed": "0xb56b6",
                "l2_gas_price": "0x1dcd65000",
                "overall_fee": "0x151eb86f3ed400",
                "unit": "FRI"
              },
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3648-3652)
```json
                    "overall_fee": {
                        "title": "Overall fee",
                        "description": "The estimated fee for the transaction (in wei or fri, depending on the tx version), equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price",
                        "$ref": "#/components/schemas/FELT"
                    },
```

**File:** crates/blockifier/src/fee/fee_checks.rs (L196-215)
```rust
    /// Checks that the actual resources used are within the bounds set by the sender.
    fn check_resources_within_bounds(
        valid_resource_bounds: &ValidResourceBounds,
        gas_vector: &GasVector,
        // TODO(Aviv): delete the tx_context parameter.
        tx_context: &TransactionContext,
    ) -> FeeCheckResult<()> {
        match valid_resource_bounds {
            ValidResourceBounds::AllResources(all_resource_bounds) => {
                // Iterate over resources and check actual_amount <= max_amount.
                FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &all_resource_bounds.to_max_amounts(),
                    gas_vector,
                )
            }
            ValidResourceBounds::L1Gas(l1_bounds) => {
                // Check that the total discounted l1 gas used <= l1_bounds.max_amount.
                FeeCheckReport::check_l1_gas_amount_within_bounds(l1_bounds, gas_vector, tx_context)
            }
        }
```
