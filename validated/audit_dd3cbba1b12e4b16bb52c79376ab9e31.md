### Title
DeleteAccount with Self-Beneficiary Permanently Burns Account Balance - (File: `runtime/runtime/src/actions.rs`)

### Summary

`action_delete_account` in nearcore's runtime does not check whether `beneficiary_id == account_id`. When a user submits a `DeleteAccount` action naming themselves as the beneficiary, the function emits a balance-refund receipt targeting the account, then removes the account from state. The refund receipt subsequently arrives at a non-existent account, fails with `AccountDoesNotExist`, and the balance is permanently burned. No validation at any layer prevents this.

### Finding Description

`action_delete_account` (`runtime/runtime/src/actions.rs`, lines 299–374) executes the following sequence unconditionally:

1. Reads `account_balance = account_ref.amount()` (line 350).
2. Pushes `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` into `result.new_receipts` (lines 351–354).
3. Calls `remove_account(state_update, account_id)` (line 356), erasing the account from the trie.
4. Sets `*account = None` (line 373). [1](#0-0) 

There is no guard of the form `if delete_account.beneficiary_id == *account_id { return; }` anywhere in this function or in the upstream validation path.

The only validation performed on `DeleteAccountAction` is:

```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;
    Ok(())
}
``` [2](#0-1) 

This only verifies that `beneficiary_id` is a syntactically valid account ID. It does not reject `beneficiary_id == account_id`.

When the emitted balance-refund receipt is later processed, `check_account_existence` is called for the `Transfer` action. Because the account was already deleted, `account.is_none()` is true, and `check_transfer_to_nonexisting_account` returns `Err(AccountDoesNotExist)` for any non-implicit account ID: [3](#0-2) [4](#0-3) 

The receipt fails. Because its `predecessor_id` is `"system"` (set by `Receipt::new_balance_refund`), there is no real account to receive a secondary refund, and the balance is permanently burned. The comment inside `check_transfer_to_nonexisting_account` itself acknowledges this path:

> "Account deletion with beneficiary creates a refund, so it'll not create a new account." [5](#0-4) 

The `debug_assert!(!is_refund)` in `action_transfer_or_implicit_account_creation` (line 2866) would fire in debug builds when a refund receipt arrives at a non-existent account, confirming the developers are aware this path exists but have not guarded against the self-beneficiary case. [6](#0-5) 

### Impact Explanation

The corrupted protocol value is the **account balance**: a user's entire unlocked NEAR balance is permanently burned rather than transferred. The trie state root after the block reflects a balance that has vanished from the total supply without being credited anywhere. This is a concrete, irreversible state corruption: the balance is neither in the deleted account nor in any beneficiary account.

### Likelihood Explanation

Any unprivileged user can submit a signed `DeleteAccount` transaction with `beneficiary_id` set to their own account ID. No special role is required. A deployed smart contract can also call `promise_batch_action_delete_account` with `beneficiary_id` equal to `env::current_account_id()`, burning the contract's own balance. The transaction passes all admission checks and is included in a block normally. [2](#0-1) 

### Recommendation

In `action_delete_account`, add an early check:

```rust
if &delete_account.beneficiary_id == account_id {
    // Skip balance transfer; balance would be sent to a deleted account.
    // Either reject the action or treat the balance as burned intentionally.
    result.result = Err(ActionErrorKind::DeleteAccountWithSelfBeneficiary {
        account_id: account_id.clone(),
    }.into());
    return Ok(());
}
```

Alternatively, add a validation-layer rejection in `validate_delete_action` that compares `beneficiary_id` against the receipt's `receiver_id`. The chosen behavior (reject vs. burn) should be explicitly specified in the protocol spec and enforced consistently.

### Proof of Concept

1. Create account `alice.near` with balance 10 NEAR.
2. Submit a signed transaction from `alice.near` to `alice.near` containing:
   ```
   Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "alice.near" })
   ```
3. The transaction passes validation (`validate_delete_action` only checks account ID syntax).
4. `action_delete_account` emits a `new_balance_refund` receipt for `alice.near` with 10 NEAR, then calls `remove_account` on `alice.near`.
5. The refund receipt is processed: `alice.near` does not exist → `AccountDoesNotExist` error → receipt fails → predecessor is `"system"` → no secondary refund → 10 NEAR permanently burned.
6. Final state: `alice.near` does not exist, 10 NEAR is missing from total supply. [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/runtime/src/actions.rs (L299-375)
```rust
pub(crate) fn action_delete_account(
    state_update: &mut TrieUpdate,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    receipt: &Receipt,
    result: &mut ActionResult,
    account_id: &AccountId,
    delete_account: &DeleteAccountAction,
    config: &RuntimeConfig,
    current_protocol_version: ProtocolVersion,
) -> Result<(), StorageError> {
    let account_ref = account.as_ref().unwrap();
    let account_storage_usage = if ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
        .enabled(current_protocol_version)
    {
        let contract_storage = get_contract_storage_usage(state_update, account_id, account_ref)?;
        account_ref.storage_usage().saturating_sub(contract_storage)
    } else {
        // Legacy behavior: only subtracts local contract code, misses the
        // global contract identifier overhead.
        let account_storage_usage = account_ref.storage_usage();
        let code_len = get_code_len_or_default(
            state_update,
            account_id.clone(),
            account_ref.local_contract_hash().unwrap_or_default(),
        )?;
        debug_assert!(
            code_len == 0 || account_storage_usage > code_len,
            "account storage usage should be larger than code size. storage usage: {}, code size: {}",
            account_storage_usage,
            code_len
        );
        account_storage_usage.saturating_sub(code_len)
    };
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
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
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L791-798)
```rust
        Action::Transfer(_) => {
            if account.is_none() {
                return check_transfer_to_nonexisting_account(
                    config,
                    account_id,
                    implicit_account_creation_eligible,
                );
            }
```

**File:** runtime/runtime/src/actions.rs (L829-848)
```rust
fn check_transfer_to_nonexisting_account(
    config: &RuntimeConfig,
    account_id: &AccountId,
    implicit_account_creation_eligible: bool,
) -> Result<(), ActionError> {
    if implicit_account_creation_eligible
        && account_is_implicit(account_id, config.wasm_config.eth_implicit_accounts)
    {
        // OK. It's implicit account creation.
        // Notes:
        // - Transfer action has to be the only action in the transaction to avoid
        // abuse by hijacking this account with other public keys or contracts.
        // - Refunds don't automatically create accounts, because refunds are free and
        // we don't want some type of abuse.
        // - Account deletion with beneficiary creates a refund, so it'll not create a
        // new account.
        Ok(())
    } else {
        Err(ActionErrorKind::AccountDoesNotExist { account_id: account_id.clone() }.into())
    }
```

**File:** runtime/runtime/src/action_validation.rs (L377-381)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/lib.rs (L2865-2867)
```rust
    } else {
        debug_assert!(!is_refund);
        action_implicit_account_creation_transfer(
```

**File:** core/primitives/src/receipt.rs (L496-510)
```rust
    pub fn new_balance_refund(receiver_id: &AccountId, refund: Balance) -> Self {
        Receipt::V0(ReceiptV0 {
            predecessor_id: "system".parse().unwrap(),
            receiver_id: receiver_id.clone(),
            receipt_id: CryptoHash::default(),
            receipt: ReceiptEnum::Action(ActionReceipt {
                signer_id: "system".parse().unwrap(),
                signer_public_key: PublicKey::empty(KeyType::ED25519),
                gas_price: Balance::ZERO,
                output_data_receivers: vec![],
                input_data_ids: vec![],
                actions: vec![Action::Transfer(TransferAction { deposit: refund })],
            }),
        })
    }
```
