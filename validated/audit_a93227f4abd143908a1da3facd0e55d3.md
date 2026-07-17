### Title
Caller's Attached Deposit Silently Absorbed in `rlp_execute` and Callback Error Paths Without Refund - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `WalletContract::rlp_execute` function is marked `#[payable]` and accepts NEAR deposits from external callers. Multiple code paths return early with a successful `Value` response (not a panic) without refunding the caller's attached deposit, causing those tokens to be permanently absorbed into the wallet contract's balance. This is the direct analog of the reported Solidity `payable` function that accepts value it does not intend to use.

### Finding Description

**Root cause 1 — `rlp_execute` early return when `has_in_flight_tx` is true:**

`rlp_execute` is `#[payable]`, so any attached deposit is immediately credited to the contract's account balance upon invocation. When `has_in_flight_tx` is `true`, the function returns a `PromiseOrValue::Value(ExecuteResponse { success: false, ... })` — a **normal return, not a panic**. In NEAR, only a panic triggers the runtime's automatic deposit refund. A normal return leaves the deposit in the contract's balance with no refund issued. [1](#0-0) 

**Root cause 2 — `nep_141_storage_balance_callback` error paths drop `caller_deposit`:**

When the ERC-20 transfer path is taken, `inner_rlp_execute` captures the caller's deposit into a `CallerDeposit` struct and passes it through the promise chain. However, three early-return error paths in `nep_141_storage_balance_callback` return `PromiseOrValue::Value(...)` without issuing any refund:

- `PromiseResult::Failed` from `storage_balance_of` (line 204–209)
- JSON deserialization failure of the storage balance response (line 213–219)
- Action is not a `FunctionCall` when `storage_deposit` is needed (line 245–253) [2](#0-1) [3](#0-2) 

**Root cause 3 — `address_check_callback` error paths drop `caller_deposit`:**

Similarly, three early-return paths in `address_check_callback` return without refunding `caller_deposit`:

- `PromiseResult::Failed` from the registrar lookup (line 142–147)
- JSON deserialization failure of the registrar response (line 151–157)
- Target address maps to an existing named account and signer is not the current account (line 167–172) [4](#0-3) 

**Contrast with the correct path:** `rlp_execute_callback` correctly issues a refund transfer promise on `PromiseResult::Failed`, demonstrating the intended design. [5](#0-4) 

**`CallerDeposit` construction** confirms the deposit is tracked for external callers with non-zero deposits: [6](#0-5) 

### Impact Explanation

An external caller (e.g., a relayer) who attaches a NEAR deposit to `rlp_execute` loses that deposit in any of the above paths. The deposit is silently absorbed into the wallet contract's account balance. The corrupted protocol value is the **caller's account balance** (decreased) and the **wallet contract's account balance** (increased without authorization). This is a direct, irreversible token loss for the caller.

### Likelihood Explanation

- The `has_in_flight_tx` path is reachable by any external caller at any time a legitimate transaction is in flight. A relayer forwarding a user deposit can race into this path.
- The `nep_141_storage_balance_callback` failure path is reachable if the NEP-141 token contract is unavailable, returns malformed JSON, or if the action type invariant is violated.
- The `address_check_callback` failure path is reachable if the address registrar contract is unavailable or returns malformed data.
- All paths are reachable by an unprivileged external user submitting a signed NEAR transaction with an attached deposit.

### Recommendation

1. In `rlp_execute`, when `has_in_flight_tx` is `true`, explicitly refund `env::attached_deposit()` to `env::predecessor_account_id()` before returning, or panic (which triggers the runtime's automatic refund).
2. In `nep_141_storage_balance_callback` and `address_check_callback`, add a refund transfer promise for `caller_deposit` in every early-return error path, mirroring the pattern already used in `rlp_execute_callback`.

### Proof of Concept

1. Deploy the wallet contract on an ETH implicit account.
2. Trigger a legitimate `rlp_execute` call so that `has_in_flight_tx = true`.
3. While the transaction is in flight, call `rlp_execute` from an external account with `attached_deposit = 5 NEAR`.
4. The function returns `ExecuteResponse { success: false, error: "transaction already in progress" }` — a normal return.
5. Observe that the caller's balance decreased by 5 NEAR and the wallet contract's balance increased by 5 NEAR, with no refund receipt generated.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-105)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-172)
```rust
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
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
        };
        let current_account_id = env::current_account_id();
        let promise = if maybe_account_id.is_some() {
            // We intentionally do not increment the nonce in this case because the
            // error is caused by a faulty relayer, not the user. An honest relayer
            // may still be able to successfully send the user's intended transaction.
            if env::signer_account_id() == current_account_id {
                create_ban_relayer_promise(current_account_id)
            } else {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
                });
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-220)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L239-253)
```rust
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
