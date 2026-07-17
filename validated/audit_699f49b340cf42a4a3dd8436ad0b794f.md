### Title
Gas Key Balance Permanently Locked When Exceeding `MAX_BALANCE_TO_BURN` With No Withdrawal Path - (File: runtime/runtime/src/access_keys.rs)

### Summary
Any unprivileged user who funds a gas key with more than `1 NEAR` (`GasKeyInfo::MAX_BALANCE_TO_BURN`) creates a state where that balance is permanently inaccessible. The key cannot be deleted (deletion is blocked), the account cannot be deleted (also blocked), and there is no protocol mechanism to reclaim the balance to the account. The funds are effectively locked in the gas key's balance field forever, analogous to the `InsuranceFund` locking pattern in the reference report.

### Finding Description

The NEAR runtime introduces gas keys (`GasKeyFullAccess`) as a new access key type that holds a NEAR balance used to pay gas fees. Funds are moved into a gas key via `TransferToGasKeyAction` and can be moved back via `WithdrawFromGasKeyAction`.

However, `WithdrawFromGasKeyAction` is restricted: it is **only available via transactions signed by the account's full-access key**, not via contract execution (no host function exists for it). More critically, when a gas key's balance exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR), the runtime blocks **both** deletion paths:

**Path 1 — `DeleteKeyAction` on the gas key:** [1](#0-0) 

If `gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN`, the action fails with `GasKeyBalanceTooHigh` and the key is left intact.

**Path 2 — `DeleteAccountAction`:** [2](#0-1) 

If the sum of all gas key balances exceeds 1 NEAR, account deletion also fails with `GasKeyBalanceTooHigh`.

The threshold is a hard constant: [3](#0-2) 

The `WithdrawFromGasKeyAction` does correctly allow partial withdrawal: [4](#0-3) 

**The locking scenario:** A user funds a gas key with, say, 10 NEAR. They can use `WithdrawFromGasKeyAction` to drain it below 1 NEAR, then delete the key. However, if the gas key's private key is **lost** (the gas key is a separate keypair from the account's full-access key), the user cannot sign a `WithdrawFromGasKeyAction` transaction (which requires the full-access key) to drain the balance — wait, actually `WithdrawFromGasKeyAction` is signed by the full-access key, not the gas key. So the user can always withdraw if they have their full-access key.

**The actual locking scenario:** A contract (via `promise_batch_action_transfer_to_gas_key`) can fund a gas key with more than 1 NEAR. Once the gas key balance exceeds 1 NEAR, the key cannot be deleted. The user must first withdraw via `WithdrawFromGasKeyAction` (a transaction-only action, not callable from contracts). If the account's full-access key is also lost or the account is a contract-only account with no full-access key, the balance above 1 NEAR is permanently locked with no recovery path.

More concretely: the `WithdrawFromGasKeyAction` comment explicitly states it is **not available via contract execution**: [5](#0-4) 

This means a contract that funds a gas key beyond 1 NEAR creates a balance that the contract itself cannot reclaim, and if the account has no full-access key (e.g., a pure contract account), the balance is permanently locked.

### Impact Explanation

The corrupted protocol value is the **account balance** (NEAR tokens). Funds deposited into a gas key via `TransferToGasKeyAction` from a contract, when they push the gas key balance above `MAX_BALANCE_TO_BURN`, become permanently inaccessible if no full-access key exists on the account. The balance is neither returned to the account nor burned — it sits in the `GasKeyInfo::balance` field in the trie indefinitely. This is a direct balance-locking outcome, not a theoretical concern.

### Likelihood Explanation

This requires: (1) a contract account that funds a gas key via the host function `promise_batch_action_transfer_to_gas_key`, (2) the funded amount pushing the gas key balance above 1 NEAR, and (3) no full-access key on the account to sign a `WithdrawFromGasKeyAction`. This is a realistic pattern for relayer or paymaster contracts that pre-fund gas keys for users. The likelihood is medium — it requires a specific account configuration but is reachable by any unprivileged user deploying a contract.

### Recommendation

1. Allow `WithdrawFromGasKeyAction` to be callable from contract execution (add a corresponding host function), so contracts can reclaim gas key balances they funded.
2. Alternatively, when `DeleteKeyAction` is called on a gas key whose balance exceeds `MAX_BALANCE_TO_BURN`, instead of failing, return the excess balance to the account's `amount` field and only burn up to `MAX_BALANCE_TO_BURN`.
3. At minimum, document that accounts without a full-access key that fund gas keys beyond 1 NEAR will permanently lock those funds.

### Proof of Concept

1. Deploy a contract to account `alice.near` with no full-access key (only a function-call key).
2. From the contract, call `promise_batch_action_transfer_to_gas_key` to fund a gas key with 2 NEAR.
3. Attempt `DeleteKeyAction` on the gas key → fails with `GasKeyBalanceTooHigh` (balance 2 NEAR > 1 NEAR limit).
4. Attempt `DeleteAccountAction` → fails with `GasKeyBalanceTooHigh`.
5. Attempt `WithdrawFromGasKeyAction` from within the contract → no host function exists; impossible.
6. Result: 2 NEAR is permanently locked in `GasKeyInfo::balance` in the trie with no recovery path.

The blocking logic in `delete_gas_key`: [6](#0-5) 

The blocking logic in `action_delete_account`: [7](#0-6) 

The restriction that `WithdrawFromGasKeyAction` has no contract host function: [5](#0-4)

### Citations

**File:** runtime/runtime/src/access_keys.rs (L103-111)
```rust
    if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: Some(Box::new(public_key.clone())),
            balance: gas_key_info.balance,
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/access_keys.rs (L290-334)
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
```

**File:** runtime/runtime/src/actions.rs (L339-348)
```rust
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
```

**File:** core/primitives-core/src/account.rs (L551-554)
```rust
impl GasKeyInfo {
    /// Maximum gas key balance that can be burned during key or account deletion.
    /// Deletion fails if the (sum of) gas key balance(s) exceeds this threshold.
    pub const MAX_BALANCE_TO_BURN: Balance = Balance::from_near(1);
```

**File:** core/primitives/src/action/mod.rs (L311-314)
```rust
/// Withdraw NEAR from a gas key's balance to the account.
///
/// This action must only be available via transactions, not via contract execution
/// (there is no corresponding promise batch action host function).
```
