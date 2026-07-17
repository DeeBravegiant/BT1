### Title
Receipt Size Limit Bypassed via `promise_return` Post-Validation Mutation - (`runtime/runtime/src/lib.rs`)

### Summary

A contract can craft a promise DAG such that a newly created receipt passes the `max_receipt_size` validation check, but is then silently enlarged beyond the limit when `output_data_receivers` are appended to it after validation. The oversized receipt is stored in state and propagated without re-validation, bypassing the protocol's receipt size invariant. This is an acknowledged open bug (nearcore issue #12606), explicitly noted in the codebase.

### Finding Description

The NEAR runtime enforces a `max_receipt_size` limit (4 MiB) on newly created receipts. Validation is performed inside the action execution loop in `apply_action_receipt`: [1](#0-0) 

This calls `validate_receipt` with `ValidateReceiptMode::NewReceipt`, which checks the serialized size of each new receipt: [2](#0-1) 

However, **after** the action loop completes, the runtime handles the `promise_return` case. When a contract returns `ReturnData::ReceiptIndex(receipt_index)`, the runtime appends the parent receipt's `output_data_receivers` directly onto the child receipt's `output_data_receivers` field: [3](#0-2) 

This mutation happens **after** `validate_receipt` has already approved the child receipt at its original size. No re-validation is performed. The child receipt is now larger than `max_receipt_size` and is stored in state and propagated to other shards.

The codebase explicitly acknowledges this bug in the `ValidateReceiptMode::ExistingReceipt` variant: [4](#0-3) 

A dedicated test confirms the bug is reproducible and that oversized receipts do appear in state: [5](#0-4) 

### Impact Explanation

The corrupted protocol value is a specific receipt stored in the DB (via `OutgoingReceipts` / incoming receipt proofs) whose serialized size exceeds `max_receipt_size`. This violates the protocol invariant that all receipts in state are within the size limit. Consequences include:

- Bandwidth limit violations: the oversized receipt is forwarded across shards, exceeding the intended per-receipt bandwidth cap.
- Stateless validation / chunk witness size: witnesses that include the oversized receipt may exceed size budgets, causing stateless validation failures or forcing nodes to handle receipts outside the protocol's designed bounds.
- The `ExistingReceipt` mode is a workaround that tolerates the violation, but the invariant is broken at the point of creation.

### Likelihood Explanation

Any unprivileged user can trigger this by:
1. Deploying a contract (standard public RPC operation).
2. Calling a method that constructs a promise DAG `[A -then-> B]` where A does `promise_return(C)` and C's args are sized to exactly `max_receipt_size - base_size`.
3. When A executes, C's `output_data_receivers` are extended with B's receivers, pushing C over the limit.

This requires no special privileges. The test contract `max_receipt_size_promise_return_method1` in `near-test-contracts` demonstrates the exact trigger path. [6](#0-5) 

### Recommendation

After the `promise_return` path appends `output_data_receivers` to the child receipt (lines 1029–1035 in `lib.rs`), re-run `validate_receipt` with `ValidateReceiptMode::NewReceipt` on the modified receipt. If the re-validation fails, treat it as a `NewReceiptValidationError` and revert the action, just as is done for receipts that fail the initial size check.

### Proof of Concept

The existing test `test_max_receipt_size_promise_return` in `test-loop-tests/src/tests/max_receipt_size.rs` is a complete proof of concept. It:
1. Deploys a contract.
2. Calls `max_receipt_size_promise_return_method1` with `args_size = max_receipt_size - base_receipt_size`.
3. Asserts via `assert_oversized_receipt_occurred` that a receipt exceeding `max_receipt_size` was stored in state and propagated. [7](#0-6)

### Citations

**File:** runtime/runtime/src/lib.rs (L855-865)
```rust
            if new_result.result.is_ok() {
                if let Err(e) = new_result.new_receipts.iter().try_for_each(|receipt| {
                    validate_receipt(
                        &apply_state.config.wasm_config.limit_config,
                        receipt,
                        apply_state.current_protocol_version,
                        ValidateReceiptMode::NewReceipt,
                    )
                }) {
                    new_result.result = Err(ActionErrorKind::NewReceiptValidationError(e).into());
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

**File:** runtime/runtime/src/verifier.rs (L533-541)
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
```

**File:** runtime/runtime/src/verifier.rs (L578-585)
```rust
    /// Used for validating older receipts that were saved in the state/received. Less strict than
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-208)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
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

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L1910-1939)
```rust
/// Do a promise_return with a large receipt.
/// The receipt has a single FunctionCall action with large args.
/// Creates DAG:
/// C[self.noop(large_args)] -then-> B[self.mark_test_completed()]
#[no_mangle]
pub unsafe fn max_receipt_size_promise_return_method2() {
    input(0);
    let mut args = vec![0u8; register_len(0) as usize];
    read_register(0, args.as_mut_ptr());
    let input_args_json: serde_json::Value = serde_json::from_slice(&args).unwrap();
    let args_size = input_args_json["args_size"].as_u64().unwrap();

    current_account_id(0);
    let current_account = vec![0u8; register_len(0) as usize];
    read_register(0, current_account.as_ptr() as _);

    let large_args = vec![0u8; args_size as usize];
    let noop_method = b"noop";
    let promise_c = promise_create(
        current_account.len() as u64,
        current_account.as_ptr() as u64,
        noop_method.len() as u64,
        noop_method.as_ptr() as u64,
        large_args.len() as u64,
        large_args.as_ptr() as u64,
        0,
        20 * TGAS,
    );

    promise_return(promise_c);
```
