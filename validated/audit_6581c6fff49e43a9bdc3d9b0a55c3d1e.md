### Title
Caller Deposit Stuck in Wallet Contract on Intermediate Cross-Contract Call Failure - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary

The `near-wallet-contract` (the protocol-level contract backing eth-implicit accounts) maintains an invariant that any NEAR tokens attached by an external caller to `rlp_execute` must be refunded if the underlying action fails. This invariant is correctly implemented in `rlp_execute_callback`, but is silently broken in two intermediate callbacks — `nep_141_storage_balance_callback` and `address_check_callback` — which return early on failure without issuing a refund promise. The caller's attached deposit is left permanently locked in the wallet contract's balance.

### Finding Description

`rlp_execute` is a `#[payable]` entry point that accepts an attached NEAR deposit from the caller (relayer or user). The deposit is captured as `CallerDeposit` and is supposed to be returned to the caller if the underlying action fails. [1](#0-0) 

The correct refund path lives in `rlp_execute_callback`: [2](#0-1) 

However, two intermediate callbacks that sit between `rlp_execute` and `rlp_execute_callback` return early on failure **without** issuing any refund:

**`nep_141_storage_balance_callback`** — triggered for ERC-20 emulation. When `storage_balance_of` returns `PromiseResult::Failed` or an unparseable response, the function returns an error `ExecuteResponse` directly, discarding `caller_deposit`: [3](#0-2) 

**`address_check_callback`** — triggered for EOA base-token transfers to potentially-registered addresses. When the address registrar lookup returns `PromiseResult::Failed` or an unparseable response, the function returns an error `ExecuteResponse` directly, discarding `caller_deposit`: [4](#0-3) 

In both cases `has_in_flight_tx` is reset to `false` at the top of the callback, so the wallet contract continues to accept new transactions, but the caller's NEAR tokens remain in the wallet contract's balance with no recovery path. [5](#0-4) [6](#0-5) 

### Impact Explanation

The corrupted protocol value is the **account balance** of the caller: NEAR tokens attached to `rlp_execute` are debited from the caller and credited to the wallet contract's balance, but never returned. Because the wallet contract exposes no withdrawal function, the only recovery path would require the wallet owner to explicitly sign a transfer action — which is impossible for an external relayer who does not control the wallet's private key. The stuck balance is a permanent, irreversible loss for the caller.

### Likelihood Explanation

Two realistic triggers exist without requiring any privileged access:

1. **Address registrar unavailability**: The address registrar is a specific on-chain contract. If it is temporarily congested, has a bug, or runs out of gas, any `rlp_execute` call for an EOA base-token transfer that goes through the registrar path will silently lose the caller's deposit.

2. **Malicious or buggy NEP-141 contract**: A user signs an Ethereum transaction targeting a NEP-141 token contract whose `storage_balance_of` method panics or returns an unexpected type. The `nep_141_storage_balance_callback` receives `PromiseResult::Failed` (or a parse error) and returns early, locking the caller's deposit. An attacker can deploy such a contract and advertise it as a token, causing any caller who attaches NEAR to lose their deposit.

Both triggers are reachable by an unprivileged external user through ordinary signed transactions submitted via public RPC.

### Recommendation

In every early-return failure branch of `nep_141_storage_balance_callback` and `address_check_callback`, add the same refund logic that `rlp_execute_callback` uses:

```rust
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund_promise = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(
        refund_promise,
        NearToken::from_yoctonear(yocto_near.into()),
    );
}
```

This mirrors the existing correct handling in `rlp_execute_callback` and restores the zero-balance invariant for all failure paths. [7](#0-6) 

### Proof of Concept

1. Deploy a NEP-141 contract whose `storage_balance_of` method always panics.
2. Register an eth-implicit wallet account on NEAR.
3. Sign an Ethereum ERC-20 transfer transaction targeting the malicious token contract.
4. Call `rlp_execute` on the wallet contract from an external relayer account, attaching 1 NEAR as deposit.
5. The wallet contract calls `storage_balance_of` on the malicious contract → `PromiseResult::Failed`.
6. `nep_141_storage_balance_callback` hits the early-return branch at line 204–209, returns `ExecuteResponse { success: false, … }` without creating any refund promise.
7. Observe: the relayer's balance decreased by 1 NEAR; the wallet contract's balance increased by 1 NEAR; no refund receipt was emitted. [8](#0-7)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-140)
```rust
        self.has_in_flight_tx = false;
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-273)
```rust
    #[private]
    pub fn nep_141_storage_balance_callback(
        &mut self,
        token_id: AccountId,
        receiver_id: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
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
        };
        let current_account_id = env::current_account_id();
        let ext = WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
            None => {
                // receiver_id is not registered so we must call `storage_deposit` first.
                let storage_deposit_args =
                    format!(r#"{{"account_id": "{receiver_id}"}}"#).into_bytes();
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
                };
                Promise::new(token_id)
                    .function_call(
                        "storage_deposit".into(),
                        storage_deposit_args,
                        NEP_141_STORAGE_DEPOSIT_AMOUNT,
                        NEP_141_STORAGE_DEPOSIT_GAS,
                    )
                    .function_call(
                        transfer_function_call.method_name,
                        transfer_function_call.args,
                        transfer_function_call.deposit,
                        transfer_function_call.gas,
                    )
                    .then(ext.rlp_execute_callback(caller_deposit))
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-316)
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

                ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Failed Near promise".into()),
                }
            }
            PromiseResult::Successful(value) => {
                ExecuteResponse { success: true, success_value: Some(value), error: None }
            }
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```
