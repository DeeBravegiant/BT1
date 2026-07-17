### Title
`SignedDelegateAction` Front-Running Causes Relayer Transaction DOS and Financial Loss - (File: `runtime/runtime/src/actions.rs`)

### Summary

NEAR's meta-transaction (`DelegateAction`) system embeds a user-signed `SignedDelegateAction` inside the relayer's publicly visible transaction. An unprivileged attacker can extract the `SignedDelegateAction` from the mempool, wrap it in their own transaction, and have it processed first. This consumes the user's nonce, causing the legitimate relayer's transaction to fail with `DelegateActionInvalidNonce` and the relayer to lose gas fees — a direct analog to the `permit()` front-running pattern.

### Finding Description

NEAR's NEP-366 meta-transaction feature allows a user to sign a `DelegateAction` off-chain and hand it to a relayer, who wraps it in a regular `SignedTransaction` and submits it to the network. The `SignedDelegateAction` — including the user's signature, nonce, and inner actions — is fully embedded in the relayer's transaction body and is publicly observable via the RPC mempool.

The `DelegateAction` struct carries a `nonce` field that is validated and consumed on-chain by `validate_delegate_action_key`: [1](#0-0) 

The nonce check in `validate_delegate_action_key` rejects any `DelegateAction` whose nonce is not strictly greater than the current on-chain access key nonce: [2](#0-1) 

On success, the nonce is immediately written back to state: [3](#0-2) 

Because the `SignedDelegateAction` is fully self-contained and cryptographically valid regardless of who submits the outer transaction, any observer can extract it from the relayer's pending transaction and re-wrap it in their own `SignedTransaction` targeting the same receiver account. If the attacker's outer transaction is included in a chunk before the legitimate relayer's transaction, the nonce is consumed by the attacker's execution path. When the legitimate relayer's transaction is subsequently processed, `validate_delegate_action_key` returns `DelegateActionInvalidNonce` and the relayer's transaction fails. [4](#0-3) 

The protocol documentation explicitly acknowledges that the `DelegateAction` nonce is the sole replay-prevention mechanism: [5](#0-4) 

### Impact Explanation

The concrete corrupted protocol values are:

- **Relayer's balance**: The relayer pays gas for a transaction that fails with `DelegateActionInvalidNonce`, a direct financial loss with no recourse.
- **Transaction outcome**: The relayer's `SignedTransaction` outcome is `ActionError::DelegateActionInvalidNonce` instead of success — a corrupted receipt outcome committed to the chain.
- **User's access key nonce**: The nonce is advanced by the attacker's transaction, not the relayer's, breaking the relayer's ability to retry or sequence further meta-transactions.

In the canonical FT-transfer relayer pattern (described in `docs/architecture/how/meta-tx.md`), the relayer's fee is encoded inside the `DelegateAction` itself. An attacker who front-runs the meta-transaction pays gas for the outer transaction but causes the legitimate relayer to lose their gas fees on the failed transaction. If the relayer's compensation is arranged outside the `DelegateAction` (a common pattern for general-purpose relayers), the attacker can additionally deny the relayer their fee entirely.

### Likelihood Explanation

Any unprivileged user with RPC access can query pending transactions, deserialize the `SignedTransaction` body, extract the embedded `SignedDelegateAction`, and re-submit it in their own outer transaction. No validator, node-admin, or trusted-service privilege is required. The attack requires only standard RPC calls and the ability to submit a transaction — both available to any network participant. The attack is deterministic: whichever outer transaction is included first wins, and an attacker can bias this by submitting immediately upon observing the relayer's transaction.

### Recommendation

Bind the `SignedDelegateAction` to a specific relayer by including the relayer's account ID or public key in the signed payload. This prevents any party other than the designated relayer from submitting the outer transaction. Alternatively, relayer infrastructure should wrap `DelegateAction` submission in a try/catch equivalent (e.g., check the current on-chain nonce before submitting, and handle `DelegateActionInvalidNonce` gracefully by re-querying the user for a fresh signature) rather than assuming the first submission will succeed.

### Proof of Concept

1. User Alice signs a `DelegateAction` (e.g., an FT transfer) with nonce `N` and sends the `SignedDelegateAction` to relayer Bob off-chain.
2. Bob constructs a `SignedTransaction` with `Action::Delegate(signed_delegate_action)` targeting Alice's account and broadcasts it via `broadcast_tx_async`.
3. Attacker Eve queries the RPC for pending transactions, deserializes Bob's transaction, and extracts the `SignedDelegateAction` (fully valid, signed by Alice).
4. Eve constructs her own `SignedTransaction` with the same `SignedDelegateAction` as the action, targeting Alice's account, and broadcasts it.
5. Eve's transaction is included in a chunk first. `apply_delegate_action` → `validate_delegate_action_key` validates Alice's nonce `N > current_nonce`, succeeds, and writes `access_key.nonce = N` to state. [3](#0-2) 

6. Bob's transaction is processed next. `validate_delegate_action_key` reads `current_nonce = N` and checks `N <= N`, returning `DelegateActionInvalidNonce`. [2](#0-1) 

7. Bob's transaction outcome is committed to the chain as a failure. Bob's gas fees are burned. Alice's intended action was executed (by Eve's transaction), but Bob received no compensation and lost gas — matching the `permit()` front-running impact class exactly.

### Citations

**File:** core/primitives/src/action/delegate.rs (L46-64)
```rust
pub struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** runtime/runtime/src/actions.rs (L422-453)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    if !signed_delegate_action.verify() {
        result.result = Err(ActionErrorKind::DelegateActionInvalidSignature.into());
        return Ok(());
    }
    let delegate_action = signed_delegate_action.delegate_action();
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
    if delegate_action.sender_id().as_str() != sender_id.as_str() {
        result.result = Err(ActionErrorKind::DelegateActionSenderDoesNotMatchTxReceiver {
            sender_id: delegate_action.sender_id().clone(),
            receiver_id: sender_id.clone(),
        }
        .into());
        return Ok(());
    }

    validate_delegate_action_key(state_update, apply_state, delegate_action, result)?;
    if result.result.is_err() {
        // Validation failed. Need to return Ok() because this is not a runtime error.
        // "result.result" will be return to the User as the action execution result.
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L604-611)
```rust
    if delegate_nonce.nonce() <= current_nonce {
        result.result = Err(ActionErrorKind::DelegateActionInvalidNonce {
            delegate_nonce: delegate_nonce.nonce(),
            ak_nonce: current_nonce,
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L685-699)
```rust
    match nonce_update {
        DelegateNonceUpdate::AccessKey => {
            access_key.nonce = delegate_nonce.nonce();
            set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
        }
        DelegateNonceUpdate::GasKey { nonce_index } => {
            set_gas_key_nonce(
                state_update,
                sender_id.clone(),
                public_key.clone(),
                nonce_index,
                delegate_nonce.nonce(),
            );
        }
    }
```

**File:** docs/architecture/how/meta-tx.md (L127-143)
```markdown
## Limitation: Accounts must be initialized

Any transaction, including meta transactions, must use NONCEs to avoid replay
attacks. The NONCE must be chosen by Alice and compared to a NONCE stored on
chain. This NONCE is stored on the access key information that gets initialized
when creating an account.

Implicit accounts don't need to be initialized in order to receive NEAR tokens,
or even $FT. This means users could own $FT but no NONCE is stored on chain for
them. This is problematic because we want to enable this exact use case with
meta transactions, but we have no NONCE to create a meta transaction.

For the MVP, the proposed solution, or work-around, is that the relayer will
have to initialize the account of Alice once if it does not exist. Note that
this cannot be done as part of the meta transaction. Instead, it will be a
separate transaction that executes first. Only then can Alice even create a
`SignedDelegateAction` with a valid NONCE.
```
