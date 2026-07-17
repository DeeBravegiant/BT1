### Title
Missing Deposit Refund in Wallet Contract Intermediate-Call Failure Paths — (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary

The `WalletContract` in nearcore's eth-implicit account system captures an external caller's attached deposit in a `CallerDeposit` struct and is supposed to refund it if the downstream cross-contract call fails. However, two intermediate callback paths — `address_check_callback` and `nep_141_storage_balance_callback` — return early on failure **without issuing the refund**, permanently trapping the caller's deposit inside the wallet contract.

### Finding Description

The entry point `rlp_execute` captures the external caller's attached deposit immediately:

```rust
let context = ExecutionContext::new(
    current_account_id.clone(),
    predecessor_account_id,
    env::attached_deposit(),
)?;
let caller_deposit = CallerDeposit::new(&context);
```

`CallerDeposit::new` records the `predecessor_account_id` and the non-zero `attached_deposit` for any external (non-self) caller. [1](#0-0) 

For two transaction kinds, an intermediate cross-contract call is made before the final action, and `caller_deposit` is threaded through:

**Case 1 — EOA base-token transfer with address check:**
```rust
address_registrar.lookup(address).then(ext.address_check_callback(
    target, action, caller_deposit,
))
``` [2](#0-1) 

Inside `address_check_callback`, if the registrar call fails:
```rust
PromiseResult::Failed => {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false,
        success_value: None,
        error: Some("Call to Address Registrar contract failed".into()),
    });
}
```
The function returns immediately. `caller_deposit` is never touched — no refund is issued. [3](#0-2) 

**Case 2 — ERC-20 transfer (NEP-141 `storage_balance_of` check):**
```rust
Promise::new(token_id.clone())
    .function_call("storage_balance_of"..., ...)
    .then(ext.nep_141_storage_balance_callback(
        token_id, receiver_id, action, caller_deposit,
    ))
``` [4](#0-3) 

Inside `nep_141_storage_balance_callback`, if `storage_balance_of` fails:
```rust
PromiseResult::Failed => {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false,
        success_value: None,
        error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
    });
}
```
Again, `caller_deposit` is silently dropped — no refund. [5](#0-4) 

By contrast, the **final** callback `rlp_execute_callback` correctly handles the refund when the actual target call fails:
```rust
PromiseResult::Failed => {
    if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
        let refund_promise = env::promise_batch_create(&account_id);
        env::promise_batch_action_transfer(refund_promise, NearToken::from_yoctonear(yocto_near.into()));
    }
    ...
}
``` [6](#0-5) 

The refund logic exists and is correct for the final step, but is entirely absent in the two intermediate failure branches.

### Impact Explanation

An external caller (typically a relayer) who attaches a non-zero deposit to `rlp_execute` loses that deposit whenever an intermediate call fails. The deposit is absorbed into the wallet contract's balance and is accessible only to the wallet owner (the Ethereum key holder), not to the relayer who provided it. The corrupted value is the **balance of the external caller** — a direct, concrete fund loss.

### Likelihood Explanation

- **`nep_141_storage_balance_callback`**: Any NEP-141 token contract that panics or does not implement `storage_balance_of` will trigger this path. A malicious token contract can be deliberately deployed to always fail this call, reliably draining any relayer deposit attached to ERC-20 transfer calls targeting it.
- **`address_check_callback`**: The address registrar is a trusted contract, so failure is less likely, but a temporary outage or contract bug would silently steal any deposit attached to a base-token transfer to an eth-implicit account.

Both paths are reachable by an unprivileged external user submitting a valid signed Ethereum transaction through a relayer.

### Recommendation

In both `address_check_callback` and `nep_141_storage_balance_callback`, issue the refund before returning on the `PromiseResult::Failed` branch, mirroring the logic already present in `rlp_execute_callback`:

```rust
PromiseResult::Failed => {
    if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
        let refund_promise = env::promise_batch_create(&account_id);
        env::promise_batch_action_transfer(
            refund_promise,
            NearToken::from_yoctonear(yocto_near.into()),
        );
    }
    return PromiseOrValue::Value(ExecuteResponse { ... });
}
```

### Proof of Concept

1. Deploy a malicious NEP-141 token contract whose `storage_balance_of` always panics.
2. Construct a valid Ethereum-signed ERC-20 transfer transaction targeting that token contract.
3. Call `rlp_execute` on a victim's eth-implicit wallet contract, attaching a non-zero deposit (e.g., 1 NEAR).
4. The wallet contract calls `storage_balance_of` on the malicious token; it fails.
5. `nep_141_storage_balance_callback` is invoked with `PromiseResult::Failed` and returns early at line 203–210 without refunding `caller_deposit`.
6. The 1 NEAR deposit is now in the wallet contract's balance, inaccessible to the relayer.

The existing test `test_caller_refunds` only covers the case where the **final** target call fails, not the intermediate-call failure paths, confirming these branches are untested. [7](#0-6)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-191)
```rust
impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-148)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-210)
```rust
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-305)
```rust
        match env::promise_result(0) {
            PromiseResult::Failed => {
                // The cross-contract call failed, refund the caller if needed
                if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
                    let refund_promise = env::promise_batch_create(&account_id);
                    env::promise_batch_action_transfer(
                        refund_promise,
                        NearToken::from_yoctonear(yocto_near.into()),
                    );
                }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L427-431)
```rust
            address_registrar.lookup(address).then(ext.address_check_callback(
                target,
                action,
                caller_deposit,
            ))
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L445-457)
```rust
            Promise::new(token_id.clone())
                .function_call(
                    "storage_balance_of".into(),
                    storage_balance_args,
                    NearToken::from_yoctonear(0),
                    NEP_141_STORAGE_BALANCE_OF_GAS,
                )
                .then(ext.nep_141_storage_balance_callback(
                    token_id,
                    receiver_id,
                    action,
                    caller_deposit,
                ))
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-213)
```rust
// An external caller gets its deposit back if the cross-contract call fails.
#[tokio::test]
async fn test_caller_refunds() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, address_registrar, .. } =
        TestContext::new().await?;

    let caller = worker.root_account()?;
    let deposit_amount = NearToken::from_near(3);
    let create_tx = |receiver_id: &AccountId, nonce: u64| {
        let method = "register";
        let args = br#"{"account_id": "birchmd.near"}"#;
        let action = Action::FunctionCall {
            receiver_id: receiver_id.to_string(),
            method_name: method.into(),
            args: args.to_vec(),
            gas: Gas::from_tgas(10).as_gas(),
            yocto_near: 0,
        };
        utils::create_signed_transaction(
            nonce,
            receiver_id,
            Wei::new_u128(deposit_amount.as_yoctonear() / (MAX_YOCTO_NEAR as u128)),
            action,
            &wallet_sk,
        )
    };

    // External caller gets a refund when the cross-contract call fails
    let pre_tx_account_balance = caller.view_account().await?.balance;
    let receiver_id: AccountId = "fake.near".parse()?;
    let result = wallet_contract
        .rlp_execute_from(
            &caller,
            receiver_id.as_str(),
            &create_tx(&receiver_id, 0),
            deposit_amount,
        )
        .await?;
    assert!(!result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );
```
