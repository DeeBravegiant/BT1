### Title
Meta-Transaction Deposit Refund Misdirection Allows Unprivileged Sender to Drain Relayer's Attached Deposit — (`runtime/runtime/src/actions.rs`)

### Summary

In `apply_delegate_action`, the inner action receipt is created with `predecessor_id = sender_id` (Alice, the delegate signer). When the inner action fails on the receiver's shard, the deposit refund is sent to `predecessor_id` (Alice), not to the relayer who actually paid the deposit. An unprivileged user (Alice) can deliberately craft a `DelegateAction` with a large attached deposit that is designed to fail on the receiver's shard, causing the relayer to permanently lose the deposit while Alice gains it.

### Finding Description

In `apply_delegate_action` in `runtime/runtime/src/actions.rs`, the inner receipt forwarded to the receiver is constructed with `predecessor_id: sender_id.clone()` — i.e., Alice's account ID: [1](#0-0) 

The comment in the same function explicitly acknowledges the misdirection: [2](#0-1) 

When the inner action receipt fails on Bob's shard, `refund_unspent_gas_and_deposits` computes `deposit_refund = total_deposit` and pushes a balance refund receipt to `receipt.balance_refund_receiver()`: [3](#0-2) [4](#0-3) 

`balance_refund_receiver()` resolves to `predecessor_id` of the inner receipt, which is Alice — not the relayer who paid: [5](#0-4) 

The protocol documentation explicitly acknowledges this as an exploitable financial incentive: [6](#0-5) 

### Impact Explanation

The corrupted protocol value is the **NEAR token balance** of both the relayer and Alice. The relayer's balance is permanently reduced by the full attached deposit amount; Alice's balance is permanently increased by the same amount. This is a concrete, on-chain balance state corruption — not a theoretical concern. The relayer's loss is Alice's gain, with no legitimate economic basis.

### Likelihood Explanation

Any user (Alice) who can interact with a relayer service can exploit this. The attack requires:
1. Alice crafts a `DelegateAction` with a large `deposit` attached to an inner `FunctionCall` that will deterministically fail on the receiver's shard (e.g., calling a non-existent method, or a method that always panics).
2. Alice submits the `SignedDelegateAction` to a relayer.
3. The relayer wraps it in a transaction and pays the deposit.
4. The inner action fails; the deposit refund flows to Alice.

This is a fully public RPC transaction path. No validator, node admin, or privileged role is required on Alice's side. The attack is repeatable and scales linearly with the deposit size the relayer is willing to attach.

### Recommendation

The inner receipt created in `apply_delegate_action` should set `predecessor_id` to the relayer's account ID (the outer receipt's `predecessor_id` / `signer_id`), not to `sender_id` (Alice). Alternatively, the protocol should introduce a `refund_to` field on the inner receipt that explicitly routes deposit refunds back to the relayer, analogous to the existing `ActionReceiptV2::refund_to` mechanism already present in the codebase: [7](#0-6) 

### Proof of Concept

1. Relayer deploys a service accepting `SignedDelegateAction` objects.
2. Alice constructs a `DelegateAction` targeting `bob.near` with inner action `FunctionCall { method_name: "nonexistent", deposit: 100_NEAR, ... }`.
3. Alice signs the `DelegateAction` and sends it to the relayer off-chain.
4. Relayer wraps it in a transaction (paying 100 NEAR deposit) and submits to the network.
5. On Alice's shard, `apply_delegate_action` creates an inner receipt with `predecessor_id = alice`.
6. On Bob's shard, the function call fails (method does not exist); `refund_unspent_gas_and_deposits` emits a deposit refund receipt to `predecessor_id = alice`.
7. Alice receives 100 NEAR; relayer loses 100 NEAR. The attack is repeatable.

### Citations

**File:** runtime/runtime/src/actions.rs (L456-469)
```rust
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```

**File:** runtime/runtime/src/actions.rs (L471-475)
```rust
    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
```

**File:** runtime/runtime/src/lib.rs (L1169-1173)
```rust
                .ok_or(IntegerOverflowError)?;
        let deposit_refund = if result.result.is_err() { total_deposit } else { Balance::ZERO };
        let gross_gas_refund = if result.result.is_err() {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
```

**File:** runtime/runtime/src/lib.rs (L1269-1273)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
```

**File:** core/primitives/src/receipt.rs (L428-430)
```rust
    pub fn balance_refund_receiver(&self) -> &AccountId {
        self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
    }
```

**File:** core/primitives/src/receipt.rs (L609-641)
```rust
/// ActionReceipt is derived from a set of Actions from `Transaction or from Receipt`
#[derive(
    BorshSerialize,
    BorshDeserialize,
    Debug,
    PartialEq,
    Eq,
    Clone,
    serde::Serialize,
    serde::Deserialize,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
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

**File:** docs/architecture/how/meta-tx.md (L232-242)
```markdown
In the world of meta transactions, this assumption is also challenged. If an
inner action requires an attached balance (for example a transfer action) then
this balance is taken from the relayer.

The relayer can see what the cost will be before submitting the meta transaction
and agrees to pay for it, so nothing wrong so far. But what if the transaction
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```
