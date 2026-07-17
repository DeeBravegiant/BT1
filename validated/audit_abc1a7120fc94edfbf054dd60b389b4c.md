### Title
Missing Authorization Check on `WithdrawFromGasKeyAction` Allows Any Account to Drain Another Account's Gas Key Balance - (File: runtime/runtime/src/access_keys.rs)

### Summary
`action_withdraw_from_gas_key` performs no check that the transaction signer is the owner of the target account. Any unprivileged user can craft a signed transaction directed at a victim account and drain that account's gas key balance, permanently disrupting any relayer service that depends on it. The funds move from the gas key to the victim's account balance (no theft), but the `gas_key_info.balance` DB entry is modified without authorization, and the gas key becomes unusable until manually refunded.

### Finding Description
`WithdrawFromGasKeyAction` is designed to let an account owner reclaim NEAR from a gas key's prepaid balance back into the account's main balance. The design comment in the source explicitly states this action "must only be available via transactions, not via contract execution." [1](#0-0) 

However, the runtime implementation of this action contains no check that the transaction signer (`signer_id`) is the same as the account being modified (`receiver_id` / `account_id`): [2](#0-1) 

The action validation function only checks that the `GasKeys` protocol feature is enabled — no authorization check is performed: [3](#0-2) [4](#0-3) 

The transaction verifier (`verify_and_charge_tx_ephemeral`) also performs no check that `signer_id == receiver_id` for this action type: [5](#0-4) 

In NEAR's transaction model, any account can send a transaction to any other account. When Alice sends a transaction with `receiver_id = Bob` and `actions = [WithdrawFromGasKey { public_key: bob_gas_key, amount: X }]`, the runtime applies the action to Bob's account: it reads Bob's gas key, subtracts `X` from `gas_key_info.balance`, and adds `X` to Bob's account balance. Alice pays only the transaction gas fee.

### Impact Explanation
The corrupted DB entry is `gas_key_info.balance` for the victim's gas key — it is set to zero by an unauthorized party. The gas key becomes unusable for gas key transactions (which require a non-zero balance to pay gas), disrupting any relayer service that pre-funded the key. The victim's account balance increases by the drained amount (no token loss), but the gas key's operational purpose is destroyed. An attacker can repeat this for every gas key on any account, as gas key public keys are visible on-chain via `ViewAccessKey` queries.

### Likelihood Explanation
Gas key public keys are publicly queryable. The attacker only needs to pay normal transaction gas fees (a few milliNEAR per attack). The attack is trivially scriptable: enumerate all gas keys on a target account, send one `WithdrawFromGasKey` transaction per key. No special privileges are required — any account with a small NEAR balance can execute this.

### Recommendation
Add an authorization check in `action_withdraw_from_gas_key` (or in `validate_withdraw_from_gas_key_action`) that enforces `signer_id == receiver_id` (i.e., the transaction must be self-directed). Alternatively, enforce this at the transaction validation layer in `verify_and_charge_tx_ephemeral` by rejecting any transaction where `tx.signer_id() != tx.receiver_id()` and the action list contains `WithdrawFromGasKey`.

### Proof of Concept
1. Alice observes that Bob's account `bob.near` has a gas key with public key `PK` and balance `B > 0` (via `ViewAccessKey` RPC).
2. Alice constructs a signed transaction:
   - `signer_id = alice.near`
   - `receiver_id = bob.near`
   - `actions = [WithdrawFromGasKey { public_key: PK, amount: B }]`
   - Signed with Alice's own full-access key
3. Alice submits the transaction via RPC.
4. The runtime validates Alice's key and nonce (both valid), then applies the action to `bob.near`.
5. `action_withdraw_from_gas_key` is called with `account_id = bob.near`: it finds the gas key, subtracts `B` from `gas_key_info.balance` (setting it to 0), and adds `B` to Bob's account balance.
6. Bob's gas key is now drained. Any subsequent gas key transaction signed with `PK` fails with `NotEnoughGasKeyBalance`.
7. Alice paid only the transaction gas fee (~a few milliNEAR). [6](#0-5) [7](#0-6)

### Citations

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

**File:** runtime/runtime/src/action_validation.rs (L174-176)
```rust
        Action::WithdrawFromGasKey(_) => {
            validate_withdraw_from_gas_key_action(current_protocol_version)
        }
```

**File:** runtime/runtime/src/action_validation.rs (L391-397)
```rust
fn validate_withdraw_from_gas_key_action(
    current_protocol_version: ProtocolVersion,
) -> Result<(), ActionsValidationError> {
    require_protocol_feature(ProtocolFeature::GasKeys, "GasKeys", current_protocol_version)?;

    Ok(())
}
```

**File:** runtime/runtime/src/verifier.rs (L269-361)
```rust
pub fn verify_and_charge_tx_ephemeral(
    config: &RuntimeConfig,
    account: &Account,
    access_key: &AccessKey,
    tx: &Transaction,
    transaction_cost: &TransactionCost,
    block_height: Option<BlockHeight>,
    pending: &PendingConstraints,
) -> TxVerdict {
    // It's the caller's responsibility to NOT call this function for transactions with
    // nonce_index (i.e. gas key transactions).
    assert!(
        tx.nonce().nonce_index().is_none(),
        "verify_and_charge_tx_ephemeral called for gas key transaction"
    );
    // Gas keys must be used via gas key transaction path (with nonce_index)
    if let Some(gas_key_info) = access_key.gas_key_info() {
        return TxVerdict::Failed(InvalidTxError::InvalidNonceIndex {
            tx_nonce_index: None,
            num_nonces: gas_key_info.num_nonces,
        });
    }
    let TransactionCost {
        gas_burnt,
        compute_burnt,
        gas_remaining,
        receipt_gas_price,
        total_cost,
        burnt_amount,
        ..
    } = *transaction_cost;
    let account_id = tx.signer_id();
    let tx_nonce = tx.nonce().nonce();
    let effective_nonce = std::cmp::max(access_key.nonce, pending.max_nonce);
    if let Err(e) = verify_nonce(tx_nonce, effective_nonce, block_height, tx.nonce_mode()) {
        return TxVerdict::Failed(e);
    }

    // saturating_sub is fine here: on the consensus path pending constraints
    // are always default (zero), so the subtraction is exact. On the RPC /
    // chunk-production path it is best-effort and does not affect consensus.
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
    if available_balance < total_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughBalance {
            signer_id: account_id.clone(),
            balance: available_balance,
            cost: total_cost,
        });
    }
    // Debit only this tx's cost, not the pending amount (which was already
    // charged in prior chunks and will be applied at execution time).
    let new_amount = account.amount().checked_sub(total_cost).unwrap();

    let new_allowance = match check_and_compute_new_allowance(
        access_key,
        account_id,
        tx.public_key(),
        total_cost,
    ) {
        Ok(a) => a,
        Err(e) => return TxVerdict::Failed(e),
    };

    match check_storage_stake(account, new_amount, config) {
        Ok(()) => {}
        Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
            return TxVerdict::Failed(InvalidTxError::LackBalanceForState {
                signer_id: account_id.clone(),
                amount,
            });
        }
        Err(StorageStakingError::StorageError(err)) => {
            return TxVerdict::Failed(StorageError::StorageInconsistentState(err).into());
        }
    };

    // Validate FunctionCall permission constraints if applicable
    if let Some(function_call_permission) = access_key.permission.function_call_permission()
        && let Err(e) = verify_function_call_permission(function_call_permission, tx)
    {
        return TxVerdict::Failed(e);
    }

    TxVerdict::Success(VerificationResult {
        gas_burnt,
        compute_burnt,
        gas_remaining,
        receipt_gas_price,
        burnt_amount,
        new_account_amount: new_amount,
        access_key_update: AccessKeyUpdate::Regular { nonce: tx_nonce, new_allowance },
    })
}
```

**File:** runtime/runtime/src/verifier.rs (L440-440)
```rust
    if available_gas_key_balance < gas_cost {
```
