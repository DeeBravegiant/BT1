### Title
Receipt Size Limit Bypass via `promise_return` Modifying Validated Receipt After Size Check - (File: `runtime/runtime/src/lib.rs`)

### Summary

An unprivileged user can cause receipts exceeding the `max_receipt_size` protocol limit (4,194,304 bytes) to be accepted, stored in state, and executed. The root cause is that the runtime modifies a receipt's `output_data_receivers` field **after** the receipt has already passed the `NewReceipt` size validation, and never re-validates the modified receipt. This is an acknowledged, unfixed bug in the production codebase.

### Finding Description

In `validate_receipt`, the size check is only applied when `mode == ValidateReceiptMode::NewReceipt`: [1](#0-0) 

When a contract calls `promise_return(receipt_index)`, the runtime enters the branch at `runtime/runtime/src/lib.rs` lines 1020–1037. It mutates the child receipt by appending the parent receipt's `output_data_receivers` to it **after** the child receipt was already validated: [2](#0-1) 

The modified receipt is then forwarded via `receipt_sink.forward_or_buffer_receipt` without any re-validation of its size. When the oversized receipt is later dequeued from the delayed receipt queue or incoming receipts, it is validated with `ValidateReceiptMode::ExistingReceipt`, which explicitly **skips** the size check. The code comment in `verifier.rs` acknowledges this is an open bug: [3](#0-2) 

The same bypass applies to the `value_return` path: a contract returning a value of size `max_receipt_size` causes a `DataReceipt` to be created that exceeds `max_receipt_size`, but `validate_data_receipt` only checks `max_length_returned_data`, not the total receipt size. [4](#0-3) 

The test suite explicitly documents and confirms both bugs are present and unfixed: [5](#0-4) [6](#0-5) 

### Impact Explanation

The concrete corrupted protocol value is the **validity decision on a receipt**: a receipt that should be rejected with `ReceiptValidationError::ReceiptSizeExceeded` is instead accepted, stored in the trie, and executed. The `max_receipt_size` invariant — a protocol-level bound introduced in version 69 — is violated. Downstream effects include:

- Oversized receipts stored in the delayed receipt queue or outgoing buffers inflate the `receipt_bytes` field of `CongestionInfo`, potentially triggering false congestion signals on the affected shard.
- The `per_receipt_storage_proof_size_limit` soft limit can be exceeded by a single oversized receipt, causing the chunk to stop processing subsequent receipts prematurely, delaying other users' receipts.
- The `main_storage_proof_size_soft_limit` for stateless validation witnesses can be exceeded. [7](#0-6) 

### Likelihood Explanation

Any unprivileged user who can deploy a contract and pay for gas can trigger this. The attacker:
1. Deploys a contract (standard operation).
2. Calls a method that creates a child receipt with args sized to `max_receipt_size - base_receipt_overhead` (just under the limit, so it passes `NewReceipt` validation).
3. Calls `promise_return` on that receipt from within a promise chain that has `output_data_receivers`.

The test contract `max_receipt_size_promise_return_method1` demonstrates the exact exploit path. No validator or node-operator privileges are required. [8](#0-7) 

### Recommendation

After mutating a receipt's `output_data_receivers` in the `promise_return` path (lines 1028–1035 of `runtime/runtime/src/lib.rs`), re-compute the receipt's Borsh-serialized size and reject the receipt with `ReceiptValidationError::ReceiptSizeExceeded` if it exceeds `limit_config.max_receipt_size`. This re-check must occur before the receipt is passed to `receipt_sink.forward_or_buffer_receipt`. The same re-check should be applied after `value_return` creates a `DataReceipt` whose total serialized size may exceed `max_receipt_size`.

### Proof of Concept

The existing test `test_max_receipt_size_promise_return` in `test-loop-tests/src/tests/max_receipt_size.rs` is a complete, runnable proof of concept. It:
1. Deploys a contract.
2. Calls `max_receipt_size_promise_return_method1` with `args_size = max_receipt_size - base_receipt_size`, creating a child receipt exactly at the limit.
3. Uses `promise_return` to trigger the `output_data_receivers` append, pushing the receipt over the limit.
4. Asserts via `assert_oversized_receipt_occurred` that an oversized receipt was indeed forwarded and stored — confirming the bypass. [9](#0-8)

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

**File:** runtime/runtime/src/verifier.rs (L573-585)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidateReceiptMode {
    /// Used for validating new receipts that were just created.
    /// More strict than `OldReceipt` mode, which has to handle older receipts.
    NewReceipt,
    /// Used for validating older receipts that were saved in the state/received. Less strict than
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
```

**File:** runtime/runtime/src/verifier.rs (L618-631)
```rust
/// Validates given data receipt. Checks validity of the length of the returned data.
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

**File:** runtime/runtime/src/lib.rs (L1019-1037)
```rust
        if !action_receipt.output_data_receivers().is_empty() {
            if let Ok(ReturnData::ReceiptIndex(receipt_index)) = result.result {
                // Modifying a new receipt instead of sending data
                match result
                    .new_receipts
                    .get_mut(receipt_index as usize)
                    .expect("the receipt for the given receipt index should exist")
                    .receipt_mut()
                {
                    ReceiptEnum::Action(new_action_receipt)
                    | ReceiptEnum::PromiseYield(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    ReceiptEnum::ActionV2(new_action_receipt)
                    | ReceiptEnum::PromiseYieldV2(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    _ => unreachable!("the receipt should be an action receipt"),
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L129-208)
```rust
#[test]
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-213)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
```

**File:** runtime/runtime/src/congestion_control.rs (L964-967)
```rust
pub(crate) fn compute_receipt_size(receipt: &Receipt) -> Result<u64, IntegerOverflowError> {
    let size = borsh::object_length(&receipt).unwrap();
    size.try_into().map_err(|_| IntegerOverflowError)
}
```
