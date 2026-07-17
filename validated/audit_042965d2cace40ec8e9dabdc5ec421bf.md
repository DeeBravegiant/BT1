### Title
Gas Key Balance Permanently Locked When Exceeding `MAX_BALANCE_TO_BURN` Threshold — (`File: runtime/runtime/src/access_keys.rs`, `runtime/runtime/src/actions.rs`)

---

### Summary

The nearcore gas key subsystem allows users to fund a gas key with NEAR tokens via `TransferToGasKeyAction`. When a gas key's balance exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR), both `DeleteKey` and `DeleteAccount` are blocked with `GasKeyBalanceTooHigh`. The only recovery path is `WithdrawFromGasKeyAction`, which requires the account owner to sign a transaction. However, if the private key for the account's full-access key is lost, or if the gas key was funded by a contract via the host function and the account has no other recovery path, the balance above 1 NEAR is permanently locked with no protocol-level escape hatch. This is the direct nearcore analog of the `ReachFactory` locked-funds pattern: user funds are transferred into a sub-balance store (`gas_key_info.balance`) with a documented withdrawal path (`WithdrawFromGasKey`), but the deletion path is blocked when the balance is too large, and no alternative recovery mechanism exists at the protocol level.

---

### Finding Description

**Funding path:** Any account owner (or a contract via the `promise_batch_action_transfer_to_gas_key` host function) can call `TransferToGasKeyAction` to deposit NEAR into a gas key's `GasKeyInfo::balance` field. There is no upper bound on how much can be deposited. [1](#0-0) 

**Deletion guard:** When `DeleteKey` is executed on a gas key, `delete_gas_key` checks whether `gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR). If so, it returns `GasKeyBalanceTooHigh` and the key is not deleted. [2](#0-1) 

**The threshold is exactly 1 NEAR:** [3](#0-2) 

**Same guard on `DeleteAccount`:** `action_delete_account` sums all gas key balances and blocks deletion if the total exceeds 1 NEAR. [4](#0-3) 

**The only recovery path is `WithdrawFromGasKeyAction`**, which moves balance from the gas key back to the account's `amount`. This requires a signed transaction from the account owner. [5](#0-4) 

**The locked state:** If a gas key accumulates more than 1 NEAR (e.g., via repeated `TransferToGasKey` calls, or via the contract host function), and the account owner loses access to their full-access key (or the gas key private key is the only key and the account has no full-access key), the balance above 1 NEAR is permanently locked. There is no protocol-level forced-withdrawal, no time-lock expiry, and no alternative deletion path. The gas key cannot be deleted, the account cannot be deleted, and the balance cannot be recovered.

---

### Impact Explanation

**Corrupted/locked protocol value:** `GasKeyInfo::balance` — NEAR tokens deposited into a gas key's balance field become permanently irrecoverable if the balance exceeds `MAX_BALANCE_TO_BURN` and the account owner loses key access.

**Concrete scenario:** A user funds a gas key with 2 NEAR via `TransferToGasKey`. The gas key's `balance` field now holds 2 NEAR. The user then loses their full-access key. They cannot call `WithdrawFromGasKey` (requires a signed tx from the account). They cannot call `DeleteKey` (blocked by `GasKeyBalanceTooHigh`). They cannot call `DeleteAccount` (also blocked). The 2 NEAR is permanently locked in the trie under `TrieKey::AccessKey`.

**Second scenario (contract-funded):** A contract calls `promise_batch_action_transfer_to_gas_key` to fund a gas key with more than 1 NEAR. If the contract logic has no withdrawal mechanism, or if the account's access keys are managed externally, the funds are locked with no recovery path. [6](#0-5) 

---

### Likelihood Explanation

**Realistic:** Gas keys are a new feature designed for relayer/meta-transaction use cases. Relayer contracts are expected to fund gas keys programmatically via the host function. A relayer that funds a gas key with more than 1 NEAR (e.g., to pre-pay for many transactions) and then loses key access, or whose contract has no withdrawal path, will have funds permanently locked. The threshold of 1 NEAR is low enough that normal usage (funding a gas key for a batch of transactions) can easily exceed it. The `TransferToGasKey` action has no cap on deposit amount. [7](#0-6) 

---

### Recommendation

1. **Remove or raise the `MAX_BALANCE_TO_BURN` guard on `DeleteKey`/`DeleteAccount`**, and instead return the gas key balance to the account's `amount` (or to a beneficiary for `DeleteAccount`) rather than burning it. The burn-on-delete design is the root cause: it creates a threshold above which deletion is blocked.

2. **Alternatively**, if the burn-on-delete design is intentional, add a protocol-level forced-withdrawal action that does not require the gas key private key — only the account's full-access key — so that a user who has lost the gas key private key can still recover the balance.

3. **Document the maximum safe funding amount** and enforce an upper bound on `TransferToGasKeyAction` deposits to prevent balances from exceeding `MAX_BALANCE_TO_BURN`.

---

### Proof of Concept

1. Alice creates a gas key: `AddKey { access_key: AccessKey::gas_key_full_access(3) }`.
2. Alice funds it with 1.000000000000000000000001 NEAR (just above threshold): `TransferToGasKey { deposit: 1_000_000_000_000_000_000_000_001 }`.
3. Alice loses her full-access key (or the account is a contract account with no full-access key).
4. Alice attempts `DeleteKey { public_key: gas_key_pk }` — fails with `GasKeyBalanceTooHigh { balance: 1_000_000_000_000_000_000_000_001 }`. [8](#0-7) 

5. Alice attempts `DeleteAccount { beneficiary_id: ... }` — fails with `GasKeyBalanceTooHigh`. [9](#0-8) 

6. `WithdrawFromGasKey` requires a signed transaction from the account owner — impossible without key access.
7. The 1+ NEAR is permanently locked in `GasKeyInfo::balance` in the trie with no recovery path. [10](#0-9)

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

**File:** runtime/runtime/src/access_keys.rs (L257-287)
```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
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

    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "gas key balance integer overflow".to_string(),
        ))
    })?;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
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

**File:** core/primitives-core/src/account.rs (L546-554)
```rust
pub struct GasKeyInfo {
    pub balance: Balance,
    pub num_nonces: NonceIndex,
}

impl GasKeyInfo {
    /// Maximum gas key balance that can be burned during key or account deletion.
    /// Deletion fails if the (sum of) gas key balance(s) exceeds this threshold.
    pub const MAX_BALANCE_TO_BURN: Balance = Balance::from_near(1);
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

**File:** core/primitives/src/errors.rs (L835-841)
```rust
    /// Gas key balance is too high to burn during deletion
    GasKeyBalanceTooHigh {
        account_id: AccountId,
        /// Set for DeleteKey (specific key), None for DeleteAccount (aggregate)
        public_key: Option<Box<PublicKey>>,
        balance: Balance,
    } = 25,
```
