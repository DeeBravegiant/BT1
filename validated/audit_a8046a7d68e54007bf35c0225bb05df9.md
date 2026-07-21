The code at the cited lines is confirmed in the repository, and the TODO comment at line 379 is a developer acknowledgment of the exact issue. Here is the analysis:

---

### Title
Fee Estimation Returns Wrong Gas Price for Pre-0.13.1 Historical Blocks — (`crates/apollo_rpc_execution/src/lib.rs`)

### Summary

`create_block_context` silently substitutes `NonzeroGasPrice::MIN` (= `GasPrice(1)`) for any zero gas price read from a stored block header. For pre-0.13.1 blocks where `l1_data_gas_price` was legitimately zero, any unprivileged `estimate_fee` / `simulate_transactions` call targeting that block receives a fee estimate computed with `l1_data_gas_price = 1` instead of `0`.

### Finding Description

In the `None` (non-pending) branch of `create_block_context`, gas prices are read directly from the stored block header: [1](#0-0) 

Those values are then used to build `GasPrices`, with a silent fallback: [2](#0-1) 

The developer TODO at line 379 explicitly acknowledges the problem:

> `// TODO(yair): What to do about blocks pre 0.13.1 where the data gas price were 0?`

`NonzeroGasPrice::new(GasPrice(0))` returns `None`; `unwrap_or(NonzeroGasPrice::MIN)` then substitutes `GasPrice(1)`. This affects all six price fields (`l1_gas_price_wei/fri`, `l1_data_gas_price_wei/fri`, `l2_gas_price_wei/fri`) whenever the stored value is zero.

### Impact Explanation

An unprivileged caller invoking `estimate_fee` or `simulate_transactions` on a pre-0.13.1 block (where `l1_data_gas_price = 0` is the canonical on-chain value) receives a fee estimate computed with `l1_data_gas_price = 1`. Any transaction that consumes L1 data gas units will have its `overall_fee` inflated by exactly `l1_data_gas_consumed × 1` instead of `0`. The returned estimate is authoritatively wrong relative to what the block's actual gas prices were.

This fits the allowed impact: **High — RPC fee estimation returns an authoritative-looking wrong value.**

### Likelihood Explanation

- Pre-0.13.1 blocks are permanently stored in any full node that synced from genesis.
- The RPC endpoint is public and unauthenticated.
- No privilege is required; any caller can specify a historical block number.
- The substitution is unconditional and silent — there is no warning or error returned.

### Recommendation

Before substituting `NonzeroGasPrice::MIN`, check the block's `StarknetVersion`. For blocks with version < 0.13.1, treat a zero `l1_data_gas_price` as the correct canonical value and either:
- Return it as-is (requires a type change to allow zero in the `BlockInfo` gas price fields for historical simulation), or
- Document and surface it as an explicit error rather than silently replacing it with `1`.

The TODO comment at line 379 already flags this as unresolved.

### Proof of Concept

1. Sync or seed a node with a block at height `N` where `starknet_version < 0.13.1` and `l1_data_gas_price = 0`.
2. Call `starknet_estimateFee` with a transaction that has non-zero L1 data gas consumption, targeting block `N`.
3. Observe that the returned `l1_data_gas_price` in the fee estimate is `1` (wei/fri), not `0`.
4. The `overall_fee` is inflated by `l1_data_gas_consumed` units compared to the correct value of `0`.

### Citations

**File:** crates/apollo_rpc_execution/src/lib.rs (L350-365)
```rust
        None => {
            let header = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_context_number)?
                .expect("Should have block header.")
                .block_header_without_hash;
            (
                header.block_number,
                header.timestamp,
                header.l1_gas_price,
                header.l1_data_gas_price,
                header.l2_gas_price,
                header.sequencer,
                header.l1_da_mode,
            )
        }
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L379-396)
```rust
        // TODO(yair): What to do about blocks pre 0.13.1 where the data gas price were 0?
        gas_prices: GasPrices {
            eth_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
            strk_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
```
