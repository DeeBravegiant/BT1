### Title
Attached NEAR Deposit Locked in `WalletContract` on `has_in_flight_tx` Early Return - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
The `rlp_execute` entry point of the `WalletContract` is marked `#[payable]`, meaning callers can attach a NEAR deposit. When `has_in_flight_tx == true`, the function returns early with a success-valued response (`PromiseOrValue::Value(...)`) without refunding the attached deposit. Because the NEAR runtime only auto-refunds deposits on a panic/failure, a successful return absorbs the deposit into the wallet contract's balance permanently. No refund promise is issued and no `CallerDeposit` is created in this code path.

### Finding Description
`rlp_execute` checks `has_in_flight_tx` at the very top of the function body:

```rust
if self.has_in_flight_tx {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false,
        ...
        error: Some("Error: transaction already in progress..."),
    });
}
```

This is a *successful* function return (not a panic). The NEAR runtime credits the attached deposit to the wallet contract's account before the function body runs; it only refunds on panic. Because the early return does not issue a refund promise and does not panic, any deposit attached by the caller is silently absorbed.

The `CallerDeposit` refund mechanism (used in `rlp_execute_callback`) is never reached in this path because no promise is created.

Three additional early-return paths in callbacks also drop `caller_deposit` without refunding:
- `address_check_callback` when the registrar promise fails (line 142–147) or returns unparseable data (line 151–157).
- `nep_141_storage_balance_callback` when `storage_balance_of` fails (line 204–209), returns unparseable data (line 211–219), or the action is not a `FunctionCall` (line 244–253).

### Impact Explanation
An external caller (e.g., a relayer) who attaches NEAR tokens to `rlp_execute` while `has_in_flight_tx == true` permanently loses those tokens to the wallet contract. The wallet contract's balance increases, but the caller has no mechanism to retrieve the deposit. The corrupted protocol value is the caller's NEAR balance (reduced) and the wallet contract's NEAR balance (increased without corresponding action).

### Likelihood Explanation
The `has_in_flight_tx` flag is set to `true` during normal operation whenever a transaction is in flight. A relayer retrying a call with an attached deposit (e.g., to fund a cross-contract call) while another transaction is pending will trigger this path. The `test_caller_refunds` test confirms that external callers do attach non-zero deposits to `rlp_execute` in the intended usage pattern. There is no view function to atomically check `has_in_flight_tx` and submit a transaction, so a race condition exists even for careful callers.

### Recommendation
In the `has_in_flight_tx` early-return branch, either:
1. Panic (which triggers an automatic NEAR runtime deposit refund), or
2. Issue an explicit refund promise before returning.

Similarly, in `address_check_callback` and `nep_141_storage_balance_callback`, all early-return error paths should issue a refund promise for `caller_deposit` before returning, mirroring the pattern already used in `rlp_execute_callback` on `PromiseResult::Failed`.

### Proof of Concept
1. Wallet contract is deployed; `has_in_flight_tx` is `false`.
2. Relayer A calls `rlp_execute` with a valid Ethereum transaction and a 3 NEAR attached deposit. `has_in_flight_tx` becomes `true`; a promise chain is created.
3. Before the promise resolves, Relayer B (or the same relayer in a concurrent batch) calls `rlp_execute` with a 5 NEAR attached deposit.
4. The `has_in_flight_tx == true` branch fires at line 97; the function returns `PromiseOrValue::Value(...)` successfully.
5. The 5 NEAR deposit is credited to the wallet contract's account. No refund promise is issued. Relayer B's 5 NEAR is permanently locked in the wallet contract. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-104)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-157)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
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
