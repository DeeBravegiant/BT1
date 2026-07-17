### Title
Arbitrary `refund_to` Account in `promise_set_refund_to` Enables Permanent Burning of Deposit Refunds — (File: `runtime/near-vm-runner/src/logic/logic.rs`)

### Summary
The `promise_set_refund_to` host function allows any deployed contract to redirect deposit refunds to an arbitrary, unvalidated `AccountId`. If the specified account does not exist on-chain, the refund receipt fails and the deposit is permanently burnt. An unprivileged attacker can deploy a malicious contract that exploits this to permanently destroy caller deposits, with no recovery path.

### Finding Description
The host function `promise_set_refund_to` in `runtime/near-vm-runner/src/logic/logic.rs` accepts an arbitrary `AccountId` as the `refund_to` destination for deposit refunds on a promise. The only validation performed is syntactic parsing of the account ID via `read_and_parse_account_id`. No check is made that the account actually exists on-chain. [1](#0-0) 

The `refund_to` field is stored in `ActionReceiptV2` and propagated to child receipts during function call execution. [2](#0-1) [3](#0-2) 

When a receipt fails, the runtime generates a deposit refund using `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)`. The `balance_refund_receiver()` method returns the `refund_to` account if set, or the predecessor otherwise. [4](#0-3) [5](#0-4) 

The protocol specification is explicit: "If the execution of a refund fails, the refund amount is burnt." [6](#0-5) 

If `refund_to` points to a non-existent account, the refund receipt fails and the deposit is permanently and irrecoverably burnt. The `set_refund_to` path in `ReceiptManager` stores the arbitrary account ID with no existence check. [7](#0-6) 

### Impact Explanation
The corrupted protocol value is the on-chain `balance` of the user who attached the deposit. An unprivileged attacker deploys a malicious contract that:
1. Accepts deposits from users via a function call.
2. Creates a cross-contract call with those deposits via `promise_batch_action_function_call`.
3. Calls `promise_set_refund_to` with a non-existent account ID (syntactically valid but not on-chain).
4. Causes the cross-contract call to fail (e.g., calling a non-existent method).

The deposit refund receipt is routed to the non-existent account, fails to execute, and the entire deposit is permanently burnt. The user's balance is permanently reduced with no recovery path. This is the direct analog to the reported "assets locked in an incompatible address" scenario.

### Likelihood Explanation
Any unprivileged user can deploy a contract on NEAR via a signed `DeployContract` transaction — explicitly a valid unprivileged attack vector. The malicious contract requires no special privileges, no validator access, and no node compromise. Users interacting with unverified contracts (new DeFi protocols, escrow services, etc.) are at risk. The attack is deterministic and requires only one transaction from the victim.

### Recommendation
- Validate that the `refund_to` account exists on-chain before allowing `promise_set_refund_to` to succeed, returning a `HostError` if the account is absent.
- Alternatively, implement a fallback in the refund generation path: if the `refund_to` account does not exist at refund time, fall back to sending the refund to the predecessor rather than burning it.

### Proof of Concept
1. Attacker deploys a malicious contract exposing a payable method:
   ```rust
   pub fn trap_deposit(&mut self) {
       let deposit = env::attached_deposit();
       // Create a cross-contract call with the full deposit
       let promise = env::promise_batch_create("any.near");
       env::promise_batch_action_function_call(
           promise, "nonexistent_method", b"", deposit.as_yoctonear(), 0
       );
       // Redirect the failure refund to a non-existent account
       env::promise_set_refund_to(promise, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
   }
   ```
2. Victim calls `trap_deposit` with a deposit of N NEAR.
3. The cross-contract call to `any.near::nonexistent_method` fails (`MethodNotFound`).
4. The runtime calls `receipt.balance_refund_receiver()` which returns `"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` (the `refund_to` value).
5. `Receipt::new_balance_refund("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", N)` is enqueued.
6. The refund receipt executes against a non-existent account and fails.
7. Per protocol: the refund amount is burnt.
8. Victim permanently loses N NEAR. The attacker pays only gas; the victim's deposit is destroyed.

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L2383-2408)
```rust
    pub fn promise_set_refund_to(
        &mut self,
        promise_idx: u64,
        account_id_len: u64,
        account_id_ptr: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_set_refund_to".to_string(),
            }
            .into());
        }
        let refund_to = self.read_and_parse_account_id(account_id_ptr, account_id_len)?;
        let promise = self
            .promises
            .get(promise_idx as usize)
            .ok_or(HostError::InvalidPromiseIndex { promise_idx })?;

        let receipt_idx = match &promise {
            Promise::Receipt(receipt_idx) => Ok(*receipt_idx),
            Promise::NotReceipt(_) => Err(HostError::CannotSetRefundToOnJointPromise),
        }?;

        self.ext.set_refund_to(receipt_idx, refund_to);
        Ok(())
```

**File:** core/primitives/src/receipt.rs (L428-430)
```rust
    pub fn balance_refund_receiver(&self) -> &AccountId {
        self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
    }
```

**File:** core/primitives/src/receipt.rs (L622-641)
```rust
pub struct ActionReceiptV2 {
    /// A signer of the original transaction
    pub signer_id: AccountId,
    /// The receiver of any balance refunds form this receipt if it is different from receiver_id.
    pub refund_to: Option<AccountId>,
    /// An access key which was used to sign the original transaction
    pub signer_public_key: PublicKey,
    /// A gas_price which has been used to buy gas in the original transaction
    pub gas_price: Balance,
    /// If present, where to route the output data
    pub output_data_receivers: Vec<DataReceiver>,
    /// A list of the input data dependencies for this Receipt to process.
    /// If all `input_data_ids` for this receipt are delivered to the account
    /// that means we have all the `ReceivedData` input which will be than converted to a
    /// `PromiseResult::Successful(value)` or `PromiseResult::Failed`
    /// depending on `ReceivedData` is `Some(_)` or `None`
    pub input_data_ids: Vec<CryptoHash>,
    /// A list of actions to process when all input_data_ids are filled
    pub actions: Vec<Action>,
}
```

**File:** runtime/runtime/src/function_call.rs (L172-180)
```rust
                let new_action_receipt = ActionReceiptV2 {
                    signer_id: action_receipt.signer_id().clone(),
                    signer_public_key: action_receipt.signer_public_key().clone(),
                    refund_to: receipt.refund_to,
                    gas_price: action_receipt.gas_price(),
                    output_data_receivers: receipt.output_data_receivers,
                    input_data_ids: receipt.input_data_ids,
                    actions: receipt.actions,
                };
```

**File:** runtime/runtime/src/lib.rs (L1269-1274)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
        }
```

**File:** docs/RuntimeSpec/Refunds.md (L12-13)
```markdown
If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/receipt_manager.rs (L698-703)
```rust
    pub(super) fn set_refund_to(&mut self, receipt_index: ReceiptIndex, refund_to: AccountId) {
        self.action_receipts
            .get_mut(receipt_index as usize)
            .expect("receipt index should be valid for setting refund_to")
            .refund_to = Some(refund_to)
    }
```
