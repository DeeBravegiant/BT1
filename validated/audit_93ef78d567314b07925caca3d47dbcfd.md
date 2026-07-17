### Title
`GasKeyFunctionCall` Permission Constraints Not Enforced in Gas Key Transaction Validation — (`runtime/runtime/src/verifier.rs`)

---

### Summary

`verify_and_charge_gas_key_tx_ephemeral` validates gas key transactions but never calls `verify_function_call_permission` for `GasKeyFunctionCall` keys. The embedded `FunctionCallPermission` (receiver_id, method_names, deposit=0, single-action) is silently ignored, so a holder of a restricted `GasKeyFunctionCall` key can submit gas key transactions to any receiver, calling any method, with any actions — bypassing all restrictions as if the key were `GasKeyFullAccess`.

---

### Finding Description

`AccessKeyPermission` has four variants:

```
FunctionCall(FunctionCallPermission)
FullAccess
GasKeyFunctionCall(GasKeyInfo, FunctionCallPermission)   // restricted gas key
GasKeyFullAccess(GasKeyInfo)                             // unrestricted gas key
``` [1](#0-0) 

For **regular** (non-gas-key) transactions, `verify_and_charge_tx_ephemeral` enforces `FunctionCallPermission` constraints at lines 346–350:

```rust
if let Some(function_call_permission) = access_key.permission.function_call_permission()
    && let Err(e) = verify_function_call_permission(function_call_permission, tx)
{
    return TxVerdict::Failed(e);
}
``` [2](#0-1) 

`verify_function_call_permission` enforces: exactly one action, that action must be `FunctionCall`, deposit must be zero, `receiver_id` must match, and `method_name` must be in the allowed list. [3](#0-2) 

For **gas key** transactions, `verify_and_charge_gas_key_tx_ephemeral` is called instead. It correctly identifies both `GasKeyFullAccess` and `GasKeyFunctionCall` via `access_key.gas_key_info()`, validates the nonce index, nonce value, and gas key balance — but **never calls `verify_function_call_permission`**: [4](#0-3) 

Regular transactions using a `GasKeyFunctionCall` key are rejected at lines 285–290 (gas keys must use the nonce_index path), so the only way to use a `GasKeyFunctionCall` key is through `verify_and_charge_gas_key_tx_ephemeral` — which skips the permission check entirely. [5](#0-4) 

---

### Impact Explanation

A holder of a `GasKeyFunctionCall` key restricted to (e.g.) `method_foo` on `token.near` can submit a gas key transaction (with `nonce_index`) that:
- Targets any `receiver_id` (not just `token.near`)
- Calls any method name (not just `method_foo`)
- Includes a non-zero deposit
- Contains multiple actions or non-`FunctionCall` actions (e.g., `Transfer`, `DeleteAccount`)

The corrupted protocol value is the **authorization decision** on the transaction: the runtime accepts and executes a transaction that violates the access key's declared permission scope. This can result in unauthorized token transfers, unauthorized contract calls, or destructive account actions performed on behalf of the account owner without their consent.

---

### Likelihood Explanation

Any user who holds a `GasKeyFunctionCall` key (whether self-created or delegated by an account owner to a dApp/relayer) can exploit this immediately by constructing a gas key transaction with `nonce_index` set and targeting an arbitrary receiver or method. No privileged access is required — only possession of the key's private key material, which is the normal precondition for using any access key.

---

### Recommendation

In `verify_and_charge_gas_key_tx_ephemeral`, after confirming the key is a gas key, add the same `FunctionCallPermission` enforcement that exists in `verify_and_charge_tx_ephemeral`:

```rust
if let Some(function_call_permission) = access_key.permission.function_call_permission()
    && let Err(e) = verify_function_call_permission(function_call_permission, tx)
{
    return TxVerdict::Failed(e);
}
```

This mirrors the fix in the referenced report: both the "protocol endpoint" path and the "remote proxy" path (here: both `GasKeyFullAccess` and `GasKeyFunctionCall`) must be subject to the appropriate constraints for their type.

---

### Proof of Concept

1. Account `alice.near` creates a `GasKeyFunctionCall` key restricted to call only `safe_method` on `allowed.near`, with zero deposit.
2. A third party (or Alice herself) holds the private key.
3. The holder constructs a `SignedTransaction` with `TransactionNonce::from_nonce_and_index(nonce+1, 0)` (gas key nonce format), `receiver_id = "victim.near"`, and `actions = [Transfer { deposit: 1_000_000 }]`.
4. The transaction passes `verify_and_charge_gas_key_tx_ephemeral`: gas key info is found, nonce index is valid, nonce is valid, gas key balance is sufficient.
5. `verify_function_call_permission` is never called; the receiver mismatch, action type mismatch, and nonzero deposit are never checked.
6. The runtime executes the transfer to `victim.near`, debiting `alice.near`'s account balance — an action the `GasKeyFunctionCall` key was never authorized to perform. [6](#0-5) [3](#0-2)

### Citations

**File:** core/primitives-core/src/account.rs (L575-586)
```rust
pub enum AccessKeyPermission {
    FunctionCall(FunctionCallPermission),
    /// Grants full access to the account.
    /// NOTE: It's used to replace account-level public keys.
    FullAccess,
    /// Gas key with limited permission to make transactions with FunctionCallActions
    /// Gas keys are a kind of access keys with a prepaid balance to pay for gas.
    GasKeyFunctionCall(GasKeyInfo, FunctionCallPermission),
    /// Gas key with full access to the account.
    /// Gas keys are a kind of access keys with a prepaid balance to pay for gas.
    GasKeyFullAccess(GasKeyInfo),
}
```

**File:** runtime/runtime/src/verifier.rs (L161-207)
```rust
/// Validates FunctionCall permission constraints:
/// - Transaction must have exactly one action
/// - Action must be FunctionCall with zero deposit
/// - Receiver must match permission's receiver
/// - Method name must be in allowed list (if list is non-empty)
fn verify_function_call_permission(
    function_call_permission: &FunctionCallPermission,
    tx: &Transaction,
) -> Result<(), InvalidTxError> {
    if tx.actions().len() != 1 {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::RequiresFullAccess,
        ));
    }
    let Some(Action::FunctionCall(function_call)) = tx.actions().get(0) else {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::RequiresFullAccess,
        ));
    };
    if function_call.deposit > Balance::ZERO {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::DepositWithFunctionCall,
        ));
    }
    let tx_receiver = tx.receiver_id();
    let ak_receiver = &function_call_permission.receiver_id;
    if tx_receiver != ak_receiver {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::ReceiverMismatch {
                tx_receiver: tx_receiver.clone(),
                ak_receiver: ak_receiver.clone(),
            },
        ));
    }
    if !function_call_permission.method_names.is_empty()
        && function_call_permission
            .method_names
            .iter()
            .all(|method_name| &function_call.method_name != method_name)
    {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::MethodNameMismatch {
                method_name: function_call.method_name.clone(),
            },
        ));
    }
    Ok(())
```

**File:** runtime/runtime/src/verifier.rs (L284-290)
```rust
    // Gas keys must be used via gas key transaction path (with nonce_index)
    if let Some(gas_key_info) = access_key.gas_key_info() {
        return TxVerdict::Failed(InvalidTxError::InvalidNonceIndex {
            tx_nonce_index: None,
            num_nonces: gas_key_info.num_nonces,
        });
    }
```

**File:** runtime/runtime/src/verifier.rs (L345-350)
```rust
    // Validate FunctionCall permission constraints if applicable
    if let Some(function_call_permission) = access_key.permission.function_call_permission()
        && let Err(e) = verify_function_call_permission(function_call_permission, tx)
    {
        return TxVerdict::Failed(e);
    }
```

**File:** runtime/runtime/src/verifier.rs (L370-450)
```rust
pub fn verify_and_charge_gas_key_tx_ephemeral(
    config: &RuntimeConfig,
    account: &Account,
    access_key: &AccessKey,
    current_nonce: Nonce,
    tx: &Transaction,
    transaction_cost: &TransactionCost,
    block_height: Option<BlockHeight>,
    pending: &PendingConstraints,
) -> TxVerdict {
    // It's the caller's responsibility to ONLY call this function for transactions with
    // nonce_index (i.e. gas key transactions).
    let Some(nonce_index) = tx.nonce().nonce_index() else {
        panic!("verify_and_charge_gas_key_tx_ephemeral called for non-gas key transaction")
    };
    let TransactionCost {
        gas_burnt,
        compute_burnt,
        gas_remaining,
        receipt_gas_price,
        burnt_amount,
        gas_cost,
        deposit_cost,
        ..
    } = *transaction_cost;
    let account_id = tx.signer_id();

    // Validate that access key is a gas key
    let Some(gas_key_info) = access_key.gas_key_info() else {
        return TxVerdict::Failed(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::AccessKeyNotFound {
                account_id: account_id.clone(),
                public_key: Box::new(tx.public_key().clone()),
            },
        ));
    };

    // Validate nonce_index is in valid range
    if nonce_index >= gas_key_info.num_nonces {
        return TxVerdict::Failed(InvalidTxError::InvalidNonceIndex {
            tx_nonce_index: Some(nonce_index),
            num_nonces: gas_key_info.num_nonces,
        });
    }

    let tx_nonce = tx.nonce().nonce();
    let effective_nonce = std::cmp::max(current_nonce, pending.max_nonce);
    if let Err(e) = verify_nonce(tx_nonce, effective_nonce, block_height, tx.nonce_mode()) {
        return TxVerdict::Failed(e);
    }

    // Check gas key has enough balance for gas costs, accounting for
    // pending gas key costs (prior gas key txs + pending WithdrawFromGasKey).
    // Unlike account balance, gas key balance only changes through transactions
    // that PTQ explicitly tracks, so pending should never exceed the balance.
    let Some(available_gas_key_balance) =
        gas_key_info.balance.checked_sub(pending.paid_from_gas_key)
    else {
        tracing::error!(
            target: "runtime",
            balance = %gas_key_info.balance,
            paid_from_gas_key = %pending.paid_from_gas_key,
            "pending gas key costs exceed gas key balance"
        );
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: Balance::ZERO,
            cost: gas_cost,
        });
    };
    if available_gas_key_balance < gas_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: available_gas_key_balance,
            cost: gas_cost,
        });
    }
    let new_gas_key_balance = gas_key_info.balance.checked_sub(gas_cost).unwrap();

    // Calculate new key balance in case of deposit failure. Charges only for the gas burned on
    // converting the transaction to a receipt.
```
