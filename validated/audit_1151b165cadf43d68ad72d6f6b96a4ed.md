### Title
Attached NEAR Deposit Permanently Absorbed in `rlp_execute` Early-Return Paths — (`File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

---

### Summary

The `rlp_execute` entry point of the Wallet Contract (deployed to every ETH-implicit account) is `#[payable]` and accepts an attached NEAR deposit. However, several early-return code paths return a `PromiseOrValue::Value(...)` without ever refunding the attached deposit. Because the function does not panic, NEAR's protocol-level deposit-refund mechanism does not trigger, and the tokens are permanently absorbed into the wallet contract's balance. This is a direct analog to the reported "ETH stuck in Trading contract" pattern: a fee/value is accepted by a payable function but only consumed in one branch, leaving it stuck in all other branches.

---

### Finding Description

`rlp_execute` is marked `#[payable]`: [1](#0-0) 

**Path 1 — `has_in_flight_tx` guard (line 97–104):**

When a transaction is already in flight, the function returns a `Value` immediately without refunding `env::attached_deposit()`: [2](#0-1) 

Any NEAR attached by the caller is silently absorbed into the wallet contract's balance. No refund promise is created.

**Path 2 — `inner_rlp_execute` error paths (line 126):**

When `inner_rlp_execute` returns a non-Relayer error (wrong nonce, invalid signature, nonce exhausted, bad account ID), the function again returns a `Value` without refunding the deposit: [3](#0-2) 

**Path 3 — Callback early returns without refunding `caller_deposit`:**

In `address_check_callback`, when the registrar call fails or returns an unexpected response, the function returns early without refunding `caller_deposit`: [4](#0-3) 

Similarly in `nep_141_storage_balance_callback`, when `storage_balance_of` fails or returns an unexpected response: [5](#0-4) 

The `CallerDeposit` refund mechanism only fires inside `rlp_execute_callback` on `PromiseResult::Failed`: [6](#0-5) 

All other error exits bypass this refund entirely.

The `CallerDeposit` struct itself confirms the intent: it is supposed to track and return the external caller's deposit on failure: [7](#0-6) 

---

### Impact Explanation

The corrupted protocol value is the **caller's NEAR token balance**. When an external account (e.g., a relayer or any user) attaches NEAR to `rlp_execute` and any of the above paths is taken, the attached tokens are permanently transferred into the wallet contract's balance with no recovery path for the caller. The wallet contract has no withdrawal function; the absorbed NEAR can only be spent by the ETH-implicit account owner via subsequent `rlp_execute` calls. The original caller has no recourse.

---

### Likelihood Explanation

Path 1 (`has_in_flight_tx`) is trivially triggerable: any external account can observe that a transaction is in flight (the state is public) and call `rlp_execute` with an attached deposit. Path 2 is triggered by ordinary user mistakes (wrong nonce). Paths 3 are triggered by transient failures of the registrar or NEP-141 contracts, which could be induced by an attacker who controls those contracts or by network congestion. All paths are reachable by an unprivileged external user via a standard signed NEAR transaction.

---

### Recommendation

1. In `rlp_execute`, before any early `return`, check `env::attached_deposit()` and if non-zero, create a refund promise back to `env::predecessor_account_id()`.
2. In `address_check_callback` and `nep_141_storage_balance_callback`, on every early-return error path, check `caller_deposit` and issue a refund transfer before returning.
3. Alternatively, assert `env::attached_deposit().is_zero()` on all non-payable code paths, or add a `#[require(!env::attached_deposit().is_zero() || condition)]` guard.

---

### Proof of Concept

1. Deploy the wallet contract to an ETH-implicit account `0xABCD...`.
2. Submit a valid `rlp_execute` call that creates a long-running cross-contract promise (sets `has_in_flight_tx = true`).
3. Before the promise resolves, submit a second `rlp_execute` call from any external account with `attached_deposit = 10 NEAR`.
4. The guard at line 97 fires, returns `PromiseOrValue::Value(ExecuteResponse { success: false, ... })` — the function does not panic, so NEAR's protocol deposit-refund does not trigger.
5. The 10 NEAR is now in the wallet contract's balance. The caller's balance is permanently reduced by 10 NEAR with no refund receipt generated.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-89)
```rust
    #[payable]
    pub fn rlp_execute(
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L97-105)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L126-127)
```rust
            Err(e) => PromiseOrValue::Value(e.into()),
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L142-158)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-220)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-192)
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
}
```
