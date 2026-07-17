### Title
Unprivileged `TransferToGasKey` Inflates Gas Key Balance Above Deletion Threshold, Blocking `DeleteKey` and `DeleteAccount` - (File: `runtime/runtime/src/access_keys.rs`)

### Summary
Any unprivileged user can send a `TransferToGasKey` action targeting any account's gas key on any account they do not own. Because `action_transfer_to_gas_key` performs no authorization check, an attacker can inflate a victim's gas key balance above `GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR). Once inflated, `delete_gas_key` returns `ActionErrorKind::GasKeyBalanceTooHigh`, permanently blocking the victim's `DeleteKey` and `DeleteAccount` operations for as long as the attacker continues to fund the key.

### Finding Description

**Root cause — missing authorization in `action_transfer_to_gas_key`**

`action_transfer_to_gas_key` in `runtime/runtime/src/access_keys.rs` looks up the gas key on the receipt receiver's account and unconditionally adds `action.deposit` to its balance:

```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else { ... };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else { ... };
    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit)...?;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
}
```

There is no check that `receipt.predecessor_id() == account_id` or any equivalent ownership guard. Any account can create a transaction with `receiver_id = victim.near` and `actions = [TransferToGasKey { public_key: victim_gas_key_pk, deposit: X }]`, and the runtime will credit `X` to the victim's gas key balance.

The same path is reachable via the `promise_batch_action_transfer_to_gas_key` host function, which also calls `ctx.ext.append_action_transfer_to_gas_key` after deducting from the calling contract's balance — no ownership check is performed there either.

**Threshold check that the attacker exploits**

`delete_gas_key` enforces:

```rust
if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... }.into());
    return Ok(());
}
```

`GasKeyInfo::MAX_BALANCE_TO_BURN` is exactly 1 NEAR. Once the attacker pushes the balance above this value, every subsequent `DeleteKey` action on that key fails with `GasKeyBalanceTooHigh`.

`action_delete_account` applies the same guard to the aggregate of all gas key balances on the account:

```rust
let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... }.into());
    return Ok(());
}
```

So a single over-funded gas key also blocks `DeleteAccount`.

**Attack flow**

1. Attacker observes that `victim.near` has a gas key with public key `gas_key_pk`.
2. Attacker submits a signed transaction:
   - `signer_id = attacker.near`, `receiver_id = victim.near`
   - `actions = [TransferToGasKey { public_key: gas_key_pk, deposit: 1_000_000_000_000_000_000_000_001 }]` (1 NEAR + 1 yoctoNEAR)
3. The runtime executes `action_transfer_to_gas_key` on `victim.near`; the gas key balance becomes `> MAX_BALANCE_TO_BURN`.
4. Every `DeleteKey { public_key: gas_key_pk }` submitted by the victim now fails with `GasKeyBalanceTooHigh`.
5. Every `DeleteAccount` submitted by the victim also fails.
6. The victim can recover by submitting `WithdrawFromGasKey` to drain the balance below the threshold, but the attacker can immediately re-fund to restore the DoS, creating a sustained griefing loop.

### Impact Explanation

**Corrupted protocol value**: The gas key balance trie entry for `(victim.near, gas_key_pk)` is inflated above `MAX_BALANCE_TO_BURN`. This causes `delete_gas_key` and `action_delete_account` to return `GasKeyBalanceTooHigh` instead of succeeding, corrupting the outcome of those receipt executions.

**Scope**: The victim loses the ability to delete their gas key or their entire account for as long as the attacker sustains the funding. The victim must pay gas for each `WithdrawFromGasKey` recovery transaction; the attacker must spend > 1 NEAR per attack cycle. This is a targeted, sustained denial-of-service on two specific runtime operations.

**Severity**: Medium — matches the external report's classification (protocol does not work for a specific operation; widely-used feature affected).

### Likelihood Explanation

High. The attack requires only a standard signed transaction with a `TransferToGasKey` action directed at any account. No special privileges, validator access, or insider knowledge is needed. The attacker's only cost is > 1 NEAR per funding cycle, which is feasible for a motivated griefing actor.

### Recommendation

Add an authorization check inside `action_transfer_to_gas_key` (and the corresponding host-function path) to verify that the receipt predecessor is the account owner:

```rust
if receipt.predecessor_id() != account_id {
    result.result = Err(ActionErrorKind::ActorNoPermission { ... }.into());
    return Ok(());
}
```

Alternatively, enforce that the post-funding balance does not exceed `MAX_BALANCE_TO_BURN`, so the action fails rather than silently inflating the balance past the deletion threshold.

### Proof of Concept

```
// Attacker constructs and submits this transaction:
SignedTransaction {
    signer_id:   "attacker.near",
    receiver_id: "victim.near",
    actions: [
        TransferToGasKey {
            public_key: <victim's gas key public key>,
            deposit:    1_000_000_000_000_000_000_000_001,  // 1 NEAR + 1 yoctoNEAR
        }
    ],
    ...
}

// After inclusion, victim attempts:
SignedTransaction {
    signer_id:   "victim.near",
    receiver_id: "victim.near",
    actions: [ DeleteKey { public_key: <gas key public key> } ],
    ...
}
// Result: ActionError { kind: GasKeyBalanceTooHigh { balance: 1_000_000_000_000_000_000_000_001 } }
```

**Key code references**:

- Missing authorization check: [1](#0-0) 
- Deletion threshold guard (`delete_gas_key`): [2](#0-1) 
- Deletion threshold guard (`action_delete_account`): [3](#0-2) 
- `MAX_BALANCE_TO_BURN` constant: [4](#0-3) 
- `GasKeyBalanceTooHigh` error definition: [5](#0-4) 
- Host-function path (also unguarded): [6](#0-5)

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

**File:** runtime/runtime/src/access_keys.rs (L257-288)
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
}
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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3071-3115)
```rust
    pub fn promise_batch_action_transfer_to_gas_key(
        &mut self,
        promise_idx: u64,
        public_key_len: u64,
        public_key_ptr: u64,
        amount_ptr: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_batch_action_transfer_to_gas_key".to_string(),
            }
            .into());
        }
        let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
        let amount = Balance::from_yoctonear(
            self.memory.get_u128(&mut self.result_state.gas_counter, amount_ptr)?,
        );
        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;
        let receiver_id = self.ext.get_receipt_receiver(receipt_idx);
        let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
        let exec = gas_key_transfer_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
        );
        let burn_base = send.base;
        let use_base =
            burn_base.gas.checked_add(exec.base.gas).ok_or(HostError::IntegerOverflow)?;
        self.result_state.gas_counter.pay_action_accumulated(
            burn_base,
            use_base,
            ActionCosts::gas_key_transfer_base,
        )?;
        let burn_byte = send.per_byte;
        let use_byte =
            burn_byte.gas.checked_add(exec.per_byte.gas).ok_or(HostError::IntegerOverflow)?;
        self.result_state.gas_counter.pay_action_accumulated(
            burn_byte,
            use_byte,
            ActionCosts::gas_key_byte,
        )?;
        self.result_state.deduct_balance(amount)?;
        self.ext.append_action_transfer_to_gas_key(receipt_idx, public_key.decode()?, amount);
        Ok(())
```
