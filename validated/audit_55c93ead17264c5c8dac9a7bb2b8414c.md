### Title
Caller Deposit Not Refunded on Early-Return Failure Paths in `address_check_callback` and `nep_141_storage_balance_callback` - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary

The `WalletContract` in nearcore's eth-wallet-contract implementation fails to refund an external caller's attached deposit in multiple early-return failure paths within `address_check_callback` and `nep_141_storage_balance_callback`. This is a direct analog to the reported invariant violation: funds (the caller's deposit) are moved into an intermediate state (the wallet contract's balance) but are not returned when certain conditions are not met, leaving them permanently stuck in the contract where the wallet owner benefits.

### Finding Description

When an external caller (relayer) calls `rlp_execute` with an attached deposit, the deposit is received by the wallet contract. The `CallerDeposit` struct tracks this deposit so it can be refunded if the cross-contract call fails. [1](#0-0) 

The deposit is passed through the promise chain as `caller_deposit`. However, in `address_check_callback` and `nep_141_storage_balance_callback`, there are multiple early-return paths that return an error response **without refunding the `caller_deposit`**:

**In `address_check_callback`:**

1. If the registrar call fails (`PromiseResult::Failed`), returns early without refunding `caller_deposit`: [2](#0-1) 

2. If JSON parsing of the registrar response fails, returns early without refunding `caller_deposit`: [3](#0-2) 

3. If `maybe_account_id.is_some()` and the signer is not the wallet contract itself, returns early without refunding `caller_deposit`: [4](#0-3) 

**In `nep_141_storage_balance_callback`:**

4. If the `storage_balance_of` call fails, returns early without refunding `caller_deposit`: [5](#0-4) 

5. If JSON parsing of the storage balance response fails, returns early without refunding `caller_deposit`: [6](#0-5) 

6. If the action is not a `FunctionCall` in the `None` branch, returns early without refunding `caller_deposit`: [7](#0-6) 

By contrast, `rlp_execute_callback` **correctly** refunds the `caller_deposit` when `PromiseResult::Failed`: [8](#0-7) 

The root cause is that the intermediate callbacks (`address_check_callback`, `nep_141_storage_balance_callback`) were written to handle their own failure modes but did not carry the refund responsibility that `rlp_execute_callback` was designed to handle. When these callbacks short-circuit before reaching `rlp_execute_callback`, the deposit is orphaned.

The NEAR protocol's automatic deposit refund only triggers when a receipt **fails**. Since `rlp_execute` **succeeds** (it returns a promise), the deposit is not automatically refunded by the protocol — it is now in the wallet contract's balance. The callback then executes and returns early without scheduling a refund, leaving the deposit permanently stuck.

### Impact Explanation

The caller's deposit (NEAR tokens) gets permanently stuck in the wallet contract's balance. The wallet contract owner (the ETH implicit account holder) benefits from these stuck funds. The relayer/caller suffers a financial loss equal to the stuck deposit amount. The corrupted protocol value is the wallet contract's account balance (inflated by the stuck deposit) and the caller's account balance (deflated by the same amount), both of which are concrete on-chain state entries.

### Likelihood Explanation

- The registrar call failing is a realistic scenario (transient network issues, registrar contract bugs, or a registrar that is temporarily unavailable).
- A malicious NEP-141 token contract could be deployed that always fails `storage_balance_of`. Whenever a relayer tries to do an ERC-20 transfer to this token with a deposit attached, the deposit is permanently stuck.
- The relayer attaches a deposit to `rlp_execute` whenever the user's Ethereum transaction includes a value transfer — this is the normal operating mode for base token and ERC-20 transfers.
- An unprivileged external user can trigger this by deploying a malicious NEP-141 contract and inducing a relayer to call `rlp_execute` targeting it with a deposit.

### Recommendation

Add refund logic to all early-return failure paths in `address_check_callback` and `nep_141_storage_balance_callback`, mirroring the pattern used in `rlp_execute_callback`:

```rust
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund_promise = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(
        refund_promise,
        NearToken::from_yoctonear(yocto_near.into()),
    );
}
```

This should be inserted before every `return PromiseOrValue::Value(ExecuteResponse { success: false, ... })` in both callbacks where `caller_deposit` is in scope.

### Proof of Concept

1. Deploy a malicious NEP-141 token contract that always panics/fails on `storage_balance_of`.
2. User signs an Ethereum ERC-20 transfer transaction targeting this token contract, with a non-zero `value` (e.g., 1 NEAR).
3. Relayer calls `rlp_execute` on the wallet contract with 1 NEAR attached as deposit.
4. `inner_rlp_execute` creates a `CallerDeposit` for the relayer and schedules `storage_balance_of` → `nep_141_storage_balance_callback`. [9](#0-8) 
5. The malicious token contract's `storage_balance_of` fails → `PromiseResult::Failed` in `nep_141_storage_balance_callback`.
6. The callback returns early at line 204–209 without refunding the 1 NEAR `caller_deposit`.
7. The 1 NEAR deposit is permanently stuck in the wallet contract's balance.
8. The wallet contract owner (ETH implicit account holder) benefits from the stuck 1 NEAR; the relayer loses it.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-191)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L142-148)
```rust
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L151-157)
```rust
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L168-173)
```rust
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L204-209)
```rust
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L213-219)
```rust
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L246-253)
```rust
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
                        });
                    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L297-305)
```rust
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
