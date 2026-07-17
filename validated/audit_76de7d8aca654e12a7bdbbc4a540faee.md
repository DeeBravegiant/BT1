### Title
Attached NEAR deposit permanently locked in wallet contract on early-return paths in `rlp_execute` and its callbacks - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `rlp_execute` function in the ETH-implicit account wallet contract is `#[payable]` and accepts NEAR deposits from external callers (relayers). Multiple early-return paths in `rlp_execute` and its callbacks (`address_check_callback`, `nep_141_storage_balance_callback`) return `PromiseOrValue::Value(...)` — a successful execution with an error response — without refunding the attached deposit to the caller. In NEAR, when a `#[payable]` function returns a value without panicking, the attached deposit is credited to the contract's account and is **not** automatically returned. The result is that the caller's deposit is permanently locked in the wallet contract with no recovery mechanism.

### Finding Description

The `rlp_execute` function is marked `#[payable]`: [1](#0-0) 

**Path 1 — `has_in_flight_tx` early return (lines 97–104):**
When a transaction is already in flight, the function returns immediately with an error value. Any deposit attached by the caller is silently absorbed into the contract's balance: [2](#0-1) 

**Path 2 — Non-relayer error in `inner_rlp_execute` (line 126):**
When `inner_rlp_execute` returns a non-relayer error (e.g., `Error::User`, `Error::AccountNonceExhausted`), the function returns early without refunding the deposit: [3](#0-2) 

**Path 3 — `address_check_callback` early returns (lines 143–172):**
When the address registrar call fails (`PromiseResult::Failed`) or returns unparseable data, the callback returns early. The `caller_deposit` argument — which carries the original caller's deposit — is never refunded: [4](#0-3) 

The same pattern applies when `maybe_account_id.is_some()` and the signer is not the current account (line 168–172), where the callback returns early without touching `caller_deposit`.

**Path 4 — `nep_141_storage_balance_callback` early returns (lines 204–252):**
When the NEP-141 `storage_balance_of` call fails or returns unexpected data, the callback returns early without refunding `caller_deposit`: [5](#0-4) 

Additionally, when the action is not a `FunctionCall` (line 246–252), the callback returns early without refunding the deposit.

By contrast, the **only** place where `caller_deposit` is refunded is inside `rlp_execute_callback` on `PromiseResult::Failed`: [6](#0-5) 

All other failure paths are unguarded.

The existing test `test_caller_refunds` confirms that external callers do attach deposits to `rlp_execute` and expect them back on failure, but it only covers the `rlp_execute_callback` path — not the early-return paths above: [7](#0-6) 

### Impact Explanation

Any NEAR deposit attached by an external caller (relayer) to `rlp_execute` is permanently locked in the wallet contract's balance whenever any of the early-return paths are triggered. The wallet contract has no withdrawal or sweep function, so the funds are irrecoverable. The corrupted protocol value is the **account balance**: the caller's balance is reduced by the deposit amount and the wallet contract's balance is inflated by the same amount with no corresponding state change that can be reversed.

### Likelihood Explanation

- **`has_in_flight_tx` race condition**: In a multi-relayer environment, two relayers can submit transactions to the same wallet contract concurrently. The second relayer's deposit is lost if the first transaction is still in flight. This is a realistic, unprivileged, externally-triggerable scenario requiring no special privileges.
- **Callback early returns**: A network error causing `storage_balance_of` or the address registrar lookup to fail (`PromiseResult::Failed`) is a normal operational condition. Any caller who attached a deposit in that call loses it.
- The function is `#[payable]` and publicly callable by any NEAR account, making the attack surface fully accessible to unprivileged users.

### Recommendation

Add explicit deposit-refund logic to every early-return path. For the `has_in_flight_tx` case:

```rust
if self.has_in_flight_tx {
    let deposit = env::attached_deposit();
    if deposit > NearToken::from_yoctonear(0) {
        let refund = env::promise_batch_create(&env::predecessor_account_id());
        env::promise_batch_action_transfer(refund, deposit);
    }
    return PromiseOrValue::Value(ExecuteResponse { success: false, ... });
}
```

Apply the same pattern to every early `return PromiseOrValue::Value(...)` in `address_check_callback` and `nep_141_storage_balance_callback` that receives a non-`None` `caller_deposit`.

### Proof of Concept

1. Deploy a wallet contract for an ETH-implicit account (standard NEAR mainnet setup).
2. Relayer A calls `rlp_execute` with a valid Ethereum transaction, setting `has_in_flight_tx = true` (the promise is now in flight).
3. Before the promise resolves, Relayer B calls `rlp_execute` on the **same** wallet contract with an attached deposit of, say, 1 NEAR.
4. The `has_in_flight_tx` guard fires at line 97; the function returns `PromiseOrValue::Value(ExecuteResponse { success: false, error: Some("transaction already in progress...") })`.
5. The NEAR runtime credits the 1 NEAR deposit to the wallet contract's account (no panic occurred, so no automatic refund).
6. Relayer B's 1 NEAR is permanently locked in the wallet contract with no recovery path. [8](#0-7)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L97-128)
```rust
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );

        match result {
            Ok(promise) => {
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => {
                let promise = create_ban_relayer_promise(current_account_id);
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(e) => PromiseOrValue::Value(e.into()),
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-158)
```rust
        self.has_in_flight_tx = false;
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
            },
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L202-220)
```rust
        self.has_in_flight_tx = false;
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
            },
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
