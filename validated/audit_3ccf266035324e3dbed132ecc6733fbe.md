### Title
Monotonic-Only Nonce Validation in `DelegateAction` Enables Signed-Message Front-Running DoS — (`runtime/runtime/src/actions.rs`)

### Summary

`DelegateAction` (NEP-366 meta transactions) is nearcore's structural analog to ERC-2612 permits: a user signs an action off-chain and any third party can wrap it in a transaction and broadcast it. Because `validate_delegate_action_key` enforces only a monotonic nonce check (`delegate_nonce > current_nonce`), an unprivileged attacker who observes two signed `DelegateAction`s with sequential nonces can submit the higher-nonce one first, permanently advancing the access key nonce past the lower-nonce one and causing the lower-nonce action to fail with `DelegateActionInvalidNonce`. The user's intended on-chain operation is silently dropped.

### Finding Description

`DelegateAction` is designed so that the user signs the action and any relayer (or any third party) can wrap it in a transaction and submit it. The `SignedDelegateAction` is embedded in the outer transaction body, making it visible in the mempool and in any public relayer API.

The nonce validation in `validate_delegate_action_key` is purely monotonic:

```rust
if delegate_nonce.nonce() <= current_nonce {
    result.result = Err(ActionErrorKind::DelegateActionInvalidNonce { ... }.into());
    return Ok(());
}
``` [1](#0-0) 

On success, the nonce is immediately committed to the trie:

```rust
access_key.nonce = delegate_nonce.nonce();
set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
``` [2](#0-1) 

Because the check is `> current_nonce` (not `== current_nonce + 1`), any nonce strictly greater than the current one is accepted. This means an attacker who holds two signed `DelegateAction`s with nonces N and N+1 can submit N+1 first. Once N+1 executes, the access key nonce becomes N+1, and the action with nonce N is permanently invalid (N ≤ N+1).

By contrast, regular `TransactionV1` already has an opt-in `NonceMode::Strict` that requires `tx_nonce == ak_nonce + 1`, preventing exactly this gap-based invalidation. No equivalent protection exists for `DelegateAction`. [3](#0-2) 

### Impact Explanation

The corrupted protocol value is `access_key.nonce` in the trie state. After the attack, the nonce is advanced to N+1, making the signed `DelegateAction` with nonce N permanently unexecutable. The action receipt that should have been generated from that `DelegateAction` is never created, meaning the user's intended on-chain state change (token transfer, contract call, key addition, etc.) never occurs.

If the two actions form a logical sequence (e.g., action N grants an approval and action N+1 uses it), the attacker can selectively suppress action N while allowing action N+1 to execute, producing an inconsistent application-layer state.

### Likelihood Explanation

The attack requires:
1. The user to sign two or more `DelegateAction`s with sequential nonces (a common pattern when a user wants to perform a sequence of operations via a relayer).
2. The attacker to observe at least the higher-nonce `SignedDelegateAction` — achievable by monitoring the mempool (the `SignedDelegateAction` is embedded in the outer transaction body) or by querying a public relayer API.
3. The attacker to submit the higher-nonce action in their own outer transaction before the intended relayer submits the lower-nonce one.

All three conditions are reachable by an unprivileged external user with no special privileges. The cost to the attacker is only the gas for the outer transaction.

### Recommendation

Apply the same `NonceMode::Strict` concept to `DelegateAction` nonce validation. When a `DelegateAction` carries a strict-mode flag, `validate_delegate_action_key` should require `delegate_nonce == current_nonce + 1` rather than `delegate_nonce > current_nonce`. This prevents an attacker from submitting a higher-nonce delegate action to invalidate a lower-nonce one.

Alternatively, document that users must never sign two `DelegateAction`s with sequential nonces and expose them to different parties, and that relayers must submit delegate actions in strict nonce order.

### Proof of Concept

1. Alice's access key nonce is 100.
2. Alice signs `DelegateAction_A` (nonce 101, action: `ft_transfer("bob", 100)`).
3. Alice signs `DelegateAction_B` (nonce 102, action: `ft_transfer("carol", 50)`).
4. Alice sends both to a public relayer. The relayer broadcasts the outer transactions; both `SignedDelegateAction`s are now visible in the mempool.
5. Attacker extracts `DelegateAction_B` (nonce 102), wraps it in their own outer transaction (attacker pays gas), and submits it.
6. `apply_delegate_action` is called: `signed_delegate_action.verify()` passes (signature is valid), `102 > 100` passes, nonce is committed: `access_key.nonce = 102`. [4](#0-3) 

7. The relayer's outer transaction containing `DelegateAction_A` (nonce 101) arrives. `validate_delegate_action_key` checks `101 <= 102` → `DelegateActionInvalidNonce`. The action receipt for `ft_transfer("bob", 100)` is never generated. [1](#0-0) 

8. Bob never receives the 100 tokens. Alice's access key nonce is permanently at 102. Alice must sign a new `DelegateAction` with nonce ≥ 103 to retry, but the attacker can repeat the attack on any new pair of signed actions.

The entry path is entirely unprivileged: the attacker submits a standard signed transaction via the public RPC (`send_tx`), containing a valid `SignedDelegateAction` extracted from the mempool. [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/runtime/src/actions.rs (L430-448)
```rust
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

**File:** runtime/runtime/src/actions.rs (L686-688)
```rust
        DelegateNonceUpdate::AccessKey => {
            access_key.nonce = delegate_nonce.nonce();
            set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
```

**File:** core/primitives/src/transaction.rs (L110-116)
```rust
pub enum NonceMode {
    /// Any nonce strictly greater than the current access key nonce (default behavior).
    #[default]
    Monotonic,
    /// Nonce must be exactly `ak_nonce + 1` (sequential ordering).
    Strict,
}
```

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

**File:** core/primitives/src/action/delegate.rs (L78-90)
```rust
pub struct SignedDelegateAction {
    pub delegate_action: DelegateAction,
    pub signature: Signature,
}

impl SignedDelegateAction {
    pub fn verify(&self) -> bool {
        let delegate_action = &self.delegate_action;
        let hash = delegate_action.get_nep461_hash();
        let public_key = &delegate_action.public_key;

        self.signature.verify(hash.as_ref(), public_key)
    }
```
