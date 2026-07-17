### Title
Attached Deposit Not Refunded on Early Failure in `rlp_execute` — (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
The `rlp_execute` entry point of the ETH-implicit wallet contract is `#[payable]` and accepts NEAR token deposits from external callers (relayers). Two early-return code paths — the `has_in_flight_tx` guard and the non-relayer error path — return a plain `PromiseOrValue::Value(...)` without issuing any refund promise. Any NEAR tokens attached by the caller in those paths are permanently credited to the wallet contract's (ETH-implicit account's) balance, causing direct financial loss for the relayer.

### Finding Description
`rlp_execute` has two paths that return before creating any promise:

**Path 1 — `has_in_flight_tx` guard** (lines 97–104):
```rust
if self.has_in_flight_tx {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false, ...
        error: Some("Error: transaction already in progress..."),
    });
}
```
No refund of `env::attached_deposit()` is issued here.

**Path 2 — non-relayer error from `inner_rlp_execute`** (line 126):
```rust
Err(e) => PromiseOrValue::Value(e.into()),
```
This covers all `Error::User(_)` cases (malformed Ethereum transaction, wrong nonce, `ExcessYoctoNear`, etc.) and `Error::Relayer(_)` when the signer is not the current account. Again, no refund.

By contrast, the `rlp_execute_callback` function correctly refunds the `CallerDeposit` when the downstream cross-contract call fails (lines 297–305):
```rust
PromiseResult::Failed => {
    if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
        let refund_promise = env::promise_batch_create(&account_id);
        env::promise_batch_action_transfer(refund_promise, NearToken::from_yoctonear(yocto_near.into()));
    }
    ...
}
```
But `CallerDeposit` is only constructed inside `inner_rlp_execute` (line 345), which is never reached in Path 1, and the refund promise is only scheduled when a downstream promise exists — not when `rlp_execute` itself returns early.

The `CallerDeposit` type explicitly tracks external-caller deposits for refund purposes:
```rust
pub fn new(context: &ExecutionContext) -> Option<Self> {
    if context.current_account_id == context.predecessor_account_id {
        return None;
    }
    NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
        account_id: context.predecessor_account_id.clone(),
        yocto_near,
    })
}
```
The design intent is clear: external deposits must be refunded on failure. The early-return paths violate this invariant.

### Impact Explanation
In NEAR, when a `#[payable]` function is called with an attached deposit, the deposit is immediately credited to the contract's account. If the function returns without explicitly transferring the deposit back, it remains in the contract's balance. For the wallet contract, this means the deposit is absorbed into the ETH-implicit account's balance — permanently transferred from the relayer to the account owner. The relayer suffers a direct, irreversible NEAR token loss. The corrupted value is the relayer's account balance and the ETH-implicit account's balance.

### Likelihood Explanation
**Path 1** (`has_in_flight_tx`): Requires a concurrent call while a transaction is in flight. In production, relayers may race or retry; the window is bounded by cross-contract call latency (one to a few blocks). A malicious account owner can also deliberately keep a transaction in flight to trap relayer deposits.

**Path 2** (user/relayer errors): Triggered by any malformed Ethereum transaction (wrong nonce, `ExcessYoctoNear`, invalid ABI encoding, unsupported action). A relayer who attaches a deposit and submits a transaction that fails validation loses the deposit. This path is reachable by any unprivileged external caller with no special privileges.

### Recommendation
Before returning from the early-failure paths, issue an explicit refund transfer to `env::predecessor_account_id()` for any non-zero `env::attached_deposit()`:

```rust
// At the top of rlp_execute, before the has_in_flight_tx check:
let deposit = env::attached_deposit();
let predecessor = env::predecessor_account_id();
let current = env::current_account_id();

let refund_if_needed = || {
    if deposit.as_yoctonear() > 0 && predecessor != current {
        let p = env::promise_batch_create(&predecessor);
        env::promise_batch_action_transfer(p, deposit);
    }
};

if self.has_in_flight_tx {
    refund_if_needed();
    return PromiseOrValue::Value(...);
}
// ... and similarly for the Err(e) => path
```

Alternatively, assert `env::attached_deposit() == expected_amount` at the start of the function and panic (which causes NEAR to automatically refund the deposit) if the amount is wrong.

### Proof of Concept

**Scenario A — `has_in_flight_tx` deposit lock:**
1. Relayer R1 calls `rlp_execute` on ETH-implicit account `0xABCD…` with a 1 NEAR deposit to fund a transfer. `has_in_flight_tx` is set to `true`.
2. Before the callback resolves, Relayer R2 calls `rlp_execute` on the same account with a 2 NEAR deposit.
3. `has_in_flight_tx == true` → function returns `PromiseOrValue::Value(...)` at line 98–104 with no refund.
4. R2's 2 NEAR is credited to `0xABCD…`'s balance. R2 loses 2 NEAR permanently.

**Scenario B — user-error deposit lock:**
1. Relayer calls `rlp_execute` with a 1 NEAR deposit and an Ethereum transaction whose nonce is already consumed (stale nonce → `Error::User(UserError::InvalidNonce)`).
2. `inner_rlp_execute` returns `Err(Error::User(_))` at line 389–392.
3. `rlp_execute` returns `PromiseOrValue::Value(e.into())` at line 126 with no refund.
4. The 1 NEAR deposit is absorbed into the wallet contract's balance. The relayer loses 1 NEAR.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-128)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-317)
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
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-192)
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
}
```
