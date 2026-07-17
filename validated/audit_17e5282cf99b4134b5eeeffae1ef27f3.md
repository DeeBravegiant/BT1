### Title
DeleteAccount balance permanently burned when `beneficiary_id` is a non-existent named account - (File: `runtime/runtime/src/actions.rs`)

### Summary

`action_delete_account` correctly guards against zero-balance payouts but does not validate that `beneficiary_id` exists on-chain. When the resulting system refund receipt is delivered to a non-existent named account, the transfer fails and the entire deleted account balance is permanently burned — an exact structural analog to the external report's "zero amount is filtered but zero/invalid address is not" pattern.

### Finding Description

In `action_delete_account`, the deleted account's balance is sent as a system refund receipt to `delete_account.beneficiary_id` without any check that the account exists:

```rust
// runtime/runtime/src/actions.rs lines 349-354
let account_balance = account_ref.amount();
if account_balance > Balance::ZERO {
    result
        .new_receipts
        .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
}
``` [1](#0-0) 

The zero-amount case is correctly handled (no receipt emitted), but a non-existent recipient is not filtered. The upstream validation only checks syntactic format:

```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;
    Ok(())
}
``` [2](#0-1) 

The resulting system refund receipt (`predecessor_id == "system"`) is routed to `beneficiary_id`. When processed, `check_account_existence` for the inner `Transfer` action returns `AccountDoesNotExist` for any non-existent named account (non-implicit accounts cannot be created by refunds — this is explicitly noted in the code): [3](#0-2) 

The failure handler in `apply_action_receipt` then burns the full deposit:

```rust
let gas_refund_result = if receipt.predecessor_id().is_system() {
    // If the refund fails tokens are burned.
    if result.result.is_err() {
        stats.balance.other_burnt_amount = safe_add_balance(
            stats.balance.other_burnt_amount,
            total_deposit(&action_receipt.actions())?,
        )?
    }
    GasRefundResult::default()
}
``` [4](#0-3) 

This is documented behavior for refund failures: [5](#0-4) 

The protocol spec for `DeleteAccountAction` lists only syntactic validation errors — existence of `beneficiary_id` is never checked: [6](#0-5) 

### Impact Explanation

The entire liquid balance of the deleted account is permanently burned (credited to `stats.balance.other_burnt_amount`) rather than transferred to the intended beneficiary. The corrupted protocol values are:

- `stats.balance.other_burnt_amount` — incorrectly inflated by the full account balance
- The deleted account's balance — permanently lost from total supply accounting perspective

This is a direct analog to the external report: the zero-balance case is correctly filtered (no receipt emitted when `account_balance == 0`), but the non-existent recipient case is not filtered, causing funds to be burned.

### Likelihood Explanation

Any unprivileged user can trigger this via a public RPC transaction:

1. A user who accidentally types a non-existent account name as `beneficiary_id` loses their entire balance silently — the `DeleteAccount` action succeeds (returns `SuccessValue`), and the burn only becomes apparent when the refund receipt fails in a subsequent block.
2. A smart contract calling `promise_batch_action_delete_account` with a `beneficiary_id` derived from external/cross-contract input (e.g., a DAO voting on a beneficiary) can have its funds burned if the voted account does not exist.

The protocol provides no pre-execution warning. The account is deleted and the funds are burned without any error surfaced to the original transaction.

### Recommendation

Mirror the external report's mitigation: before emitting the balance refund receipt in `action_delete_account`, verify that `beneficiary_id` exists in the current state. If it does not exist, either:

1. Reject the `DeleteAccount` action with a new `ActionErrorKind::BeneficiaryDoesNotExist`, or
2. Fall back to sending the balance to `receipt.predecessor_id()` (the account that initiated the delete), consistent with how deposit refunds work for failed receipts.

### Proof of Concept

1. User `alice.near` holds 100 NEAR and submits:
   ```
   DeleteAccount { beneficiary_id: "nonexistent-account.near" }
   ```
2. `action_delete_account` passes `validate_delete_action` (syntactically valid account ID) and emits:
   ```
   Receipt::new_balance_refund("nonexistent-account.near", 100 NEAR)
   ``` [7](#0-6) 
3. `alice.near` is deleted from state. The refund receipt is queued.
4. In the next block, the refund receipt is processed. `check_account_existence` for the `Transfer` action finds `nonexistent-account.near` does not exist and is not an implicit account → `AccountDoesNotExist` error.
5. `apply_action_receipt` detects `predecessor_id == "system"` and `result.result.is_err()` → burns 100 NEAR into `other_burnt_amount`.
6. Alice's 100 NEAR is permanently destroyed. The original `DeleteAccount` transaction outcome shows `SuccessValue`, giving no indication of the loss.

### Citations

**File:** runtime/runtime/src/actions.rs (L349-355)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
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

**File:** runtime/runtime/src/lib.rs (L914-922)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
            GasRefundResult::default()
```

**File:** docs/RuntimeSpec/Refunds.md (L12-13)
```markdown
If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** docs/RuntimeSpec/Actions.md (L278-318)
```markdown
## DeleteAccountAction

```rust
pub struct DeleteAccountAction {
    /// The remaining account balance will be transferred to the AccountId below
    pub beneficiary_id: AccountId,
}
```

**Outcomes**:

- The account, as well as all the data stored under the account, is deleted and the tokens are transferred to `beneficiary_id`.

### Errors

**Validation Error**:

- If `beneficiary_id` is not a valid account id, the following error will be returned

```rust
/// Invalid account ID.
InvalidAccountId { account_id: AccountId },
```

- If this action is not the last action in the action list of a receipt, the following error will be returned

```rust
/// The delete action must be a final action in transaction
DeleteActionMustBeFinal
```

- If the account still has locked balance due to staking, the following error will be returned

```rust
/// Account is staking and can not be deleted
DeleteAccountStaking { account_id: AccountId }
```

**Execution Error**:

- If state or storage is corrupted, a `StorageError` is returned.
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
