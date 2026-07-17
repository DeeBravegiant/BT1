### Title
Caller Deposit Permanently Lost in Intermediate Callback Error Paths - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
`WalletContract::address_check_callback` and `WalletContract::nep_141_storage_balance_callback` contain multiple early-return error paths that silently drop the `CallerDeposit` (NEAR tokens attached by an external relayer) without issuing a refund. The final callback `rlp_execute_callback` correctly refunds on failure, but the intermediate callbacks do not, permanently locking the relayer's NEAR balance in the wallet contract with no recovery path.

### Finding Description
When an external caller (relayer) attaches a NEAR deposit to `rlp_execute`, `inner_rlp_execute` captures it in a `CallerDeposit` struct:

```rust
let caller_deposit = CallerDeposit::new(&context);
```

`CallerDeposit::new` records `predecessor_account_id` and the full `attached_deposit`:

```rust
NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
    account_id: context.predecessor_account_id.clone(),
    yocto_near,
})
```

This `caller_deposit` is threaded through the promise chain. `rlp_execute_callback` correctly refunds it on failure:

```rust
PromiseResult::Failed => {
    if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
        let refund_promise = env::promise_batch_create(&account_id);
        env::promise_batch_action_transfer(refund_promise, NearToken::from_yoctonear(yocto_near.into()));
    }
    ...
}
```

However, the two intermediate callbacks have early-return paths that drop `caller_deposit` without any refund:

**`address_check_callback`** — three paths silently drop the deposit:
- `PromiseResult::Failed` (registrar call fails): returns at line 143–148 with no refund
- JSON deserialization failure of registrar response: returns at line 152–157 with no refund
- `maybe_account_id.is_some()` and signer is not the wallet account (faulty external relayer): returns at line 168–173 with no refund

**`nep_141_storage_balance_callback`** — three paths silently drop the deposit:
- `PromiseResult::Failed` (storage_balance_of call fails): returns at line 204–209 with no refund
- JSON deserialization failure of storage balance response: returns at line 212–219 with no refund
- Action is not a `FunctionCall` (unexpected action type): returns at line 245–253 with no refund

In all six paths, the `CallerDeposit` is simply dropped. Rust's `Drop` performs no on-chain action, so the NEAR tokens remain permanently locked in the wallet contract's account balance. There is no withdrawal function or recovery mechanism.

### Impact Explanation
The relayer's NEAR deposit — which represents the value the user intended to transfer (e.g., the deposit for a `ft_transfer` call) — is permanently locked in the wallet contract. The corrupted protocol value is the **balance** of the relayer's NEAR account: it is reduced by the full `CallerDeposit::yocto_near` amount with no corresponding credit anywhere. Since the wallet contract exposes no administrative withdrawal function, the funds are irrecoverable.

### Likelihood Explanation
An unprivileged wallet owner (user) can trigger the `nep_141_storage_balance_callback` path by signing an Ethereum ERC-20 transfer transaction targeting a malicious or buggy NEP-141 contract that panics on `storage_balance_of`. The relayer, trusting the user's signed transaction, attaches a deposit and calls `rlp_execute`. The `storage_balance_of` call fails, `nep_141_storage_balance_callback` returns early, and the deposit is permanently lost. The `address_check_callback` path is triggered whenever the address registrar is temporarily unavailable or returns unexpected data. Both paths are reachable through ordinary public RPC transactions signed by the wallet owner.

### Recommendation
All six early-return error paths in `address_check_callback` and `nep_141_storage_balance_callback` must issue a refund before returning, mirroring the pattern already used in `rlp_execute_callback`:

```rust
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund_promise = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(refund_promise, NearToken::from_yoctonear(yocto_near.into()));
}
```

### Proof of Concept
1. Deploy a malicious NEP-141 contract whose `storage_balance_of` method always panics.
2. Wallet owner signs an Ethereum ERC-20 transfer transaction (`ERC20_TRANSFER_SELECTOR`) targeting the malicious contract, with a non-zero `value`.
3. Relayer calls `rlp_execute` on the wallet contract, attaching the user's value as a NEAR deposit.
4. `inner_rlp_execute` creates `CallerDeposit { account_id: relayer, yocto_near: deposit }` and routes to the `EthEmulationKind::ERC20Transfer` branch, scheduling `storage_balance_of` chained to `nep_141_storage_balance_callback`.
5. The malicious contract panics; `PromiseResult::Failed` is delivered to `nep_141_storage_balance_callback`.
6. The callback returns early at line 204–209 — `caller_deposit` is dropped with no refund promise created.
7. The relayer's NEAR deposit is permanently locked in the wallet contract's balance. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-158)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L174-192)
```rust
        } else {
            // We must increment the nonce at this point to prevent replay of the transaction.
            // Recall that the nonce was not incremented in `inner_rlp_execute` in the case that
            // the registrar contract was called (i.e. in the case we end up inside this callback).
            self.nonce = self.nonce.saturating_add(1);
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            match action_to_promise(target, action)
                .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
            {
                Ok(p) => p,
                Err(e) => {
                    return PromiseOrValue::Value(e.into());
                }
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-219)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L243-253)
```rust
                let transfer_function_call = match action {
                    near_action::Action::FunctionCall(x) => x,
                    _ => {
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
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
