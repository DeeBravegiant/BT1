### Title
Gas Key Balance Race Condition: `WithdrawFromGasKey` Receipt Delay Allows Relayer to Drain Full Balance Before Reduction Takes Effect - (File: `runtime/runtime/src/access_keys.rs`)

### Summary

The `WithdrawFromGasKey` action is executed as a receipt (deferred), not at transaction-processing time. This creates a TOCTOU window identical in structure to the ERC20 `approve` race condition: a gas key holder (relayer) who observes a pending `WithdrawFromGasKey` transaction can front-run it by submitting gas key transactions that consume the full current balance before the withdrawal receipt executes. The withdrawal receipt then fails with `InsufficientGasKeyBalance`, and the account owner loses the funds they intended to reclaim.

### Finding Description

**Gas key balance deduction is split across two phases:**

1. **Transaction processing phase** (immediate): When a gas key transaction is processed, `validate_verify_and_charge_transaction` deducts `gas_cost` from the gas key balance in the trie state. [1](#0-0) 

2. **Receipt execution phase** (deferred, next chunk or later): When a `WithdrawFromGasKey` transaction is processed, it only creates a receipt. The actual balance deduction happens later when `action_withdraw_from_gas_key` is called during receipt execution. [2](#0-1) [3](#0-2) 

**The race window:** Between the block where the `WithdrawFromGasKey` tx is included (creating a receipt) and the block where that receipt executes, the gas key balance in the certified trie state is still the original full value `B`. Any gas key transaction validated during this window sees the full balance.

**The consensus path uses zero pending constraints:** The comment in `verify_and_charge_gas_key_tx_ephemeral` explicitly states that on the consensus path, `PendingConstraints` are always default (zero), so no pending withdrawal is subtracted: [4](#0-3) 

The Pending Transaction Queue (PTQ) that tracks `WithdrawFromGasKey` amounts is only active under the `protocol_feature_spice` flag and is explicitly described as best-effort, not affecting consensus: [5](#0-4) [6](#0-5) 

**The `WithdrawFromGasKey` action is transaction-only** (no host function), so the only way to reduce a gas key balance is via this deferred receipt path: [7](#0-6) 

### Impact Explanation

**Corrupted protocol value:** The gas key balance stored in the trie state (`GasKeyInfo.balance` inside the `AccessKey` trie entry). The account owner's intended post-withdrawal balance (`small`) is never reached; instead the balance is driven to zero by the relayer's front-running gas key transactions, and the `WithdrawFromGasKey` receipt fails with `InsufficientGasKeyBalance`.

**Concrete scenario:**
- Gas key balance `B = 10 NEAR`; account owner intends to withdraw `9 NEAR`, leaving `1 NEAR` for the relayer
- Relayer observes the pending `WithdrawFromGasKey(9 NEAR)` transaction in the mempool
- Relayer submits gas key transactions (across multiple nonce indices) consuming up to `10 NEAR` in gas costs before the withdrawal receipt executes
- `WithdrawFromGasKey` receipt executes and fails: `InsufficientGasKeyBalance { balance: 0, required: 9 }`
- Account owner loses `9 NEAR` (the intended withdrawal); relayer spent `10 NEAR` instead of the intended `1 NEAR`

The account balance is unaffected; the loss is bounded by the gas key balance. However, gas keys are designed to hold meaningful NEAR to fund relayer operations, so the loss can be significant.

### Likelihood Explanation

**Medium.** Requires: (1) the account owner to have delegated a gas key private key to a third-party relayer, (2) the account owner to attempt to reduce the relayer's spending limit via `WithdrawFromGasKey`, and (3) the relayer to observe the pending transaction (trivially possible since NEAR transactions are public) and submit gas key transactions before the receipt executes (one block window, ~1 second). This is a realistic operational scenario for gas key relayer deployments.

Without `protocol_feature_spice` enabled, there is zero protocol-level protection. With SPICE, the PTQ provides RPC-level admission control but does not affect consensus validation.

### Recommendation

**Short term:** Deduct the `WithdrawFromGasKey` amount from the gas key balance at **transaction processing time** (when the tx is converted to a receipt), not at receipt execution time. This mirrors how account balance is deducted for deposits at transaction processing time. The receipt execution would then only need to verify the deduction already applied.

**Long term:** Expose `increaseGasKeyBalance` / `decreaseGasKeyBalance` semantics (analogous to ERC20's `increaseAllowance`/`decreaseAllowance`) so that reducing a gas key balance is a relative operation rather than a two-step read-then-set, eliminating the race window entirely.

### Proof of Concept

```
State: gas_key.balance = B = 10 NEAR

Block N:
  Tx A (access key): WithdrawFromGasKey { public_key: GK, amount: 9 NEAR }
    → processed: creates receipt R_A (gas key balance unchanged = 10 NEAR)
  Tx B (gas key, nonce_index=0): Transfer { deposit: 0 }
    → validated against trie state: balance=10 NEAR, gas_cost=C1
    → gas key balance deducted: 10 - C1
  Tx C (gas key, nonce_index=1): Transfer { deposit: 0 }
    → validated against ephemeral state: balance=10-C1, gas_cost=C2
    → gas key balance deducted: 10 - C1 - C2
  ... (repeat for all nonce indices until balance ≈ 0)

Block N+1:
  Receipt R_A executes: action_withdraw_from_gas_key(amount=9)
    → gas_key_info.balance.checked_sub(9) → None (balance < 9)
    → result.result = Err(InsufficientGasKeyBalance { balance: ~0, required: 9 })
    → WITHDRAWAL FAILS

Final state: gas_key.balance ≈ 0 (instead of intended 1 NEAR)
Account owner lost ~9 NEAR; relayer spent ~10 NEAR instead of intended ~1 NEAR.
```

Root cause entry point: `action_withdraw_from_gas_key` in `runtime/runtime/src/access_keys.rs` line 315 performs a `checked_sub` at receipt execution time against the already-depleted balance, rather than reserving the withdrawal amount at transaction submission time. [8](#0-7)

### Citations

**File:** runtime/runtime/src/verifier.rs (L307-310)
```rust
    // saturating_sub is fine here: on the consensus path pending constraints
    // are always default (zero), so the subtraction is exact. On the RPC /
    // chunk-production path it is best-effort and does not affect consensus.
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
```

**File:** runtime/runtime/src/verifier.rs (L421-426)
```rust
    // Check gas key has enough balance for gas costs, accounting for
    // pending gas key costs (prior gas key txs + pending WithdrawFromGasKey).
    // Unlike account balance, gas key balance only changes through transactions
    // that PTQ explicitly tracks, so pending should never exceed the balance.
    let Some(available_gas_key_balance) =
        gas_key_info.balance.checked_sub(pending.paid_from_gas_key)
```

**File:** runtime/runtime/src/verifier.rs (L447-447)
```rust
    let new_gas_key_balance = gas_key_info.balance.checked_sub(gas_cost).unwrap();
```

**File:** runtime/runtime/src/access_keys.rs (L290-335)
```rust
pub(crate) fn action_withdraw_from_gas_key(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &WithdrawFromGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    let Some(updated_balance) = gas_key_info.balance.checked_sub(action.amount) else {
        result.result = Err(ActionErrorKind::InsufficientGasKeyBalance {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
            balance: gas_key_info.balance,
            required: action.amount,
        }
        .into());
        return Ok(());
    };
    gas_key_info.balance = updated_balance;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);

    let new_account_balance = account.amount().checked_add(action.amount).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "Account balance integer overflow".to_string(),
        ))
    })?;
    account.set_amount(new_account_balance);
    Ok(())
}
```

**File:** runtime/runtime/src/lib.rs (L756-765)
```rust
            Action::WithdrawFromGasKey(withdraw_from_gas_key) => {
                metrics::ACTION_CALLED_COUNT.withdraw_from_gas_key.inc();
                action_withdraw_from_gas_key(
                    state_update,
                    account.as_mut().expect(EXPECT_ACCOUNT_EXISTS),
                    &mut result,
                    account_id,
                    withdraw_from_gas_key,
                )?;
            }
```

**File:** chain/client/src/pending_transaction_queue.rs (L279-288)
```rust
            // Scan actions for WithdrawFromGasKey (affects gas key balance).
            for action in tx.actions() {
                if let Action::WithdrawFromGasKey(withdraw) = action {
                    let gas_key_entry = chunk_data
                        .gas_key_costs
                        .entry((signer_id.clone(), withdraw.public_key.clone()))
                        .or_insert(Balance::ZERO);
                    *gas_key_entry = gas_key_entry.saturating_add(withdraw.amount);
                }
            }
```

**File:** core/primitives/src/action/mod.rs (L311-332)
```rust
/// Withdraw NEAR from a gas key's balance to the account.
///
/// This action must only be available via transactions, not via contract execution
/// (there is no corresponding promise batch action host function).
#[derive(
    BorshSerialize,
    BorshDeserialize,
    PartialEq,
    Eq,
    Clone,
    Debug,
    serde::Serialize,
    serde::Deserialize,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct WithdrawFromGasKeyAction {
    /// The public key of the gas key to withdraw from
    pub public_key: PublicKey,
    /// Amount of NEAR to transfer from the gas key
    pub amount: Balance,
}
```
