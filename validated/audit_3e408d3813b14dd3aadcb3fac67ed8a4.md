### Title
Receipt `max_receipt_size` Limit Not Enforced After Post-Validation Mutation — (`File: runtime/runtime/src/verifier.rs`)

### Summary

The NEAR runtime validates a new receipt's total borsh-serialized size against `max_receipt_size` at the moment the receipt is first constructed. However, two code paths allow the receipt to be **mutated after that check**, causing the final receipt stored in state and forwarded cross-shard to exceed `max_receipt_size`. This is an acknowledged open bug (nearcore issue #12606) with a concrete, unprivileged trigger path and measurable protocol impact on congestion-control bandwidth accounting and `ChunkStateWitness` size invariants.

### Finding Description

`validate_receipt` in `runtime/runtime/src/verifier.rs` enforces `max_receipt_size` only when called with `ValidateReceiptMode::NewReceipt`:

```rust
if mode == ValidateReceiptMode::NewReceipt {
    let receipt_size: u64 = borsh::object_length(receipt)...;
    if receipt_size > limit_config.max_receipt_size {
        return Err(ReceiptValidationError::ReceiptSizeExceeded { ... });
    }
}
``` [1](#0-0) 

Two paths allow a receipt to grow past this limit after the check passes:

**Path 1 — `promise_return` with a near-max-size receipt:**
A contract creates receipt C whose size equals exactly `max_receipt_size`. The receipt passes `NewReceipt` validation. The contract then calls `promise_return(C)`, which causes the runtime to append `output_data_receivers` entries to C's `ActionReceipt`. The receipt is never re-validated; its final borsh size exceeds `max_receipt_size`.

**Path 2 — `value_return` with a large value:**
`value_return` checks only `num_bytes > max_length_returned_data` (the raw payload length):

```rust
if num_bytes > self.config.limit_config.max_length_returned_data {
    return Err(HostError::ReturnedValueLengthExceeded { ... });
}
``` [2](#0-1) 

The runtime then wraps this payload in a `DataReceipt` struct. `validate_data_receipt` also only checks the raw data length, not the total borsh size of the `DataReceipt`:

```rust
fn validate_data_receipt(...) -> Result<(), ReceiptValidationError> {
    let data_len = receipt.data.as_ref().map(|data| data.len()).unwrap_or(0);
    if data_len as u64 > limit_config.max_length_returned_data { ... }
    Ok(())
}
``` [3](#0-2) 

The resulting `DataReceipt` borsh size (data + `data_id` + framing) exceeds `max_receipt_size` without any check.

The codebase explicitly acknowledges both bugs. `ValidateReceiptMode::ExistingReceipt` was introduced specifically to tolerate these oversized receipts already in state:

```
2) There is a bug which allows to create receipts that are above the size limit. Runtime has
   to handle them gracefully until the receipt size limit bug is fixed.
   See https://github.com/near/nearcore/issues/12606 for details.
``` [4](#0-3) 

The congestion-control forwarding path in `try_forward` contains a compensating workaround that clamps the receipt size to `max_receipt_size` for bandwidth accounting:

```rust
// There is a bug which allows to create receipts that are above the size limit.
// ...Let's pretend that all receipts are at most `max_receipt_size`...
if size > max_receipt_size {
    size = max_receipt_size;
}
``` [5](#0-4) 

This clamping means the actual bytes forwarded cross-shard exceed the granted bandwidth, corrupting the bandwidth scheduler's accounting.

### Impact Explanation

1. **`max_receipt_size` invariant violated in state**: Receipts larger than 4 MiB (the `max_receipt_size` limit) are stored in the delayed receipt queue and forwarded cross-shard. The `ChunkStateWitness` size bound (designed to stay under ~17 MiB) can be exceeded, since a single receipt can now be arbitrarily larger than 4 MiB.

2. **Bandwidth scheduler accounting corrupted**: `try_forward` clamps the size to `max_receipt_size` when deducting from the outgoing bandwidth grant, but the actual bytes sent are larger. This means a shard can send more bytes than its granted bandwidth, violating the bandwidth scheduler's cross-shard flow-control invariant and corrupting the `OutgoingLimit.size` accounting.

3. **Congestion info corrupted**: The `congestion_size` stored in `StateStoredReceipt` metadata reflects the clamped value, not the true receipt size, causing `CongestionInfo` to undercount actual memory consumption.

The corrupted protocol values are: `OutgoingLimit.size` (bandwidth grant accounting), `CongestionInfo.receipt_bytes`, and the `ChunkStateWitness` total size.

### Likelihood Explanation

Any unprivileged user who can deploy a contract can trigger this. The trigger requires:
1. Deploy a contract (standard operation, costs gas)
2. Call a method that creates a near-max-size receipt and uses `promise_return` or `value_return` with a large payload

No validator, node admin, or special privilege is required. The test suite explicitly demonstrates both paths work: [6](#0-5) [7](#0-6) 

### Recommendation

Re-validate the total borsh-serialized receipt size **after** all post-execution mutations are applied (i.e., after `output_data_receivers` are appended and after `DataReceipt` is constructed from a `value_return` payload). Specifically:

- After appending `output_data_receivers` in the `promise_return` resolution path, re-check `borsh::object_length(&receipt) <= max_receipt_size` and fail the action if exceeded.
- In `validate_data_receipt`, additionally check the total borsh size of the `DataReceipt` struct (not just the raw data length) against `max_receipt_size`.

### Proof of Concept

The nearcore test suite itself demonstrates both paths. The test `test_max_receipt_size_promise_return` constructs a receipt of exactly `max_receipt_size`, calls `promise_return`, and then asserts that an oversized receipt was observed in the chain: [8](#0-7) 

The test `test_max_receipt_size_value_return` does the same for the `value_return` path: [9](#0-8) 

Both tests call `assert_oversized_receipt_occurred`, which walks the chain and confirms that a receipt with `borsh_size > max_receipt_size` was actually forwarded and stored — proving the limit is not enforced end-to-end.

### Citations

**File:** runtime/runtime/src/verifier.rs (L533-542)
```rust
    if mode == ValidateReceiptMode::NewReceipt {
        let receipt_size: u64 =
            borsh::object_length(receipt).unwrap().try_into().expect("Can't convert usize to u64");
        if receipt_size > limit_config.max_receipt_size {
            return Err(ReceiptValidationError::ReceiptSizeExceeded {
                size: receipt_size,
                limit: limit_config.max_receipt_size,
            });
        }
    }
```

**File:** runtime/runtime/src/verifier.rs (L579-585)
```rust
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
```

**File:** runtime/runtime/src/verifier.rs (L619-631)
```rust
fn validate_data_receipt(
    limit_config: &LimitConfig,
    receipt: &DataReceipt,
) -> Result<(), ReceiptValidationError> {
    let data_len = receipt.data.as_ref().map(|data| data.len()).unwrap_or(0);
    if data_len as u64 > limit_config.max_length_returned_data {
        return Err(ReceiptValidationError::ReturnedValueLengthExceeded {
            length: data_len as u64,
            limit: limit_config.max_length_returned_data,
        });
    }
    Ok(())
}
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3882-3888)
```rust
        if num_bytes > self.config.limit_config.max_length_returned_data {
            return Err(HostError::ReturnedValueLengthExceeded {
                length: num_bytes,
                limit: self.config.limit_config.max_length_returned_data,
            }
            .into());
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L413-427)
```rust
        // There is a bug which allows to create receipts that are above the size limit. Receipts
        // above the size limit might not fit under the maximum outgoing size limit. Let's pretend
        // that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
        // See https://github.com/near/nearcore/issues/12606
        let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
        if size > max_receipt_size {
            tracing::debug!(
                target: "runtime",
                receipt_id=?receipt.receipt_id(),
                size,
                max_receipt_size,
                "try_forward observed a receipt with size exceeding the size limit",
            );
            size = max_receipt_size;
        }
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L130-208)
```rust
fn test_max_receipt_size_promise_return() {
    init_test_logger();

    let account = create_account_id("account0");
    let account_signer = create_user_test_signer(&account);
    let mut env = TestLoopBuilder::new()
        .enable_rpc()
        .add_user_account(&account, Balance::from_near(10_000))
        .build();

    // Deploy the test contract
    let deploy_contract_tx = SignedTransaction::deploy_contract(
        101,
        &account,
        near_test_contracts::rs_contract().into(),
        &account_signer,
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(deploy_contract_tx, Duration::seconds(5));

    // User calls a contract method
    // Contract method creates a DAG with two promises: [A -then-> B]
    // When promise A is executed, it creates a third promise - `C` and does a `promise_return`.
    // The DAG changes to: [C ->then-> B]
    // The receipt for promise C is a maximum size receipt.
    // Adding the `output_data_receivers` to C's receipt makes it go over the size limit.
    let base_receipt_template = Receipt::V0(ReceiptV0 {
        predecessor_id: account.clone(),
        receiver_id: account.clone(),
        receipt_id: CryptoHash::default(),
        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: account.clone(),
            signer_public_key: account_signer.public_key().into(),
            gas_price: Balance::ZERO,
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: vec![Action::FunctionCall(Box::new(FunctionCallAction {
                method_name: "noop".into(),
                args: vec![],
                gas: Gas::ZERO,
                deposit: Balance::ZERO,
            }))],
        }),
    });
    let base_receipt_template = action_receipt_v1_to_latest(&base_receipt_template);
    let base_receipt_size = borsh::object_length(&base_receipt_template).unwrap();
    let max_receipt_size = 4_194_304;
    let args_size = max_receipt_size - base_receipt_size;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_promise_return_method1".into(),
        format!("{{\"args_size\": {}}}", args_size).into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(large_receipt_tx, Duration::seconds(5));

    // Make sure that the last promise in the DAG was called
    let assert_test_completed = SignedTransaction::call(
        103,
        account.clone(),
        account,
        &account_signer,
        Balance::ZERO,
        "assert_test_completed".into(),
        "".into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(assert_test_completed, Duration::seconds(5));

    assert_oversized_receipt_occurred(&env.validator());
}
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-215)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
#[test]
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L216-267)
```rust
fn test_max_receipt_size_value_return() {
    init_test_logger();

    let account = create_account_id("account0");
    let account_signer = create_user_test_signer(&account);
    let mut env = TestLoopBuilder::new()
        .enable_rpc()
        .add_user_account(&account, Balance::from_near(10_000))
        .build();

    // Deploy the test contract
    let deploy_contract_tx = SignedTransaction::deploy_contract(
        101,
        &account,
        near_test_contracts::rs_contract().into(),
        &account_signer,
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(deploy_contract_tx, Duration::seconds(5));

    let max_receipt_size = 4_194_304;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_value_return_method".into(),
        format!("{{\"value_size\": {}}}", max_receipt_size).into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(large_receipt_tx, Duration::seconds(5));

    // Make sure that the last promise in the DAG was called
    let assert_test_completed = SignedTransaction::call(
        103,
        account.clone(),
        account,
        &account_signer,
        Balance::ZERO,
        "assert_test_completed".into(),
        "".into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(assert_test_completed, Duration::seconds(5));

    assert_oversized_receipt_occurred(&env.validator());
}
```
