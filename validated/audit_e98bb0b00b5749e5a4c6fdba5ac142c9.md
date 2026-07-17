### Title
DelegateAction Executed at Exactly `max_block_height` Despite Protocol Spec Requiring Expiry at That Height - (File: `runtime/runtime/src/actions.rs`)

### Summary
The `apply_delegate_action` function uses a strict `>` comparison instead of `>=` when checking whether a `DelegateAction` has expired. The protocol specification explicitly states that the action should be rejected when the current block height is **equal to or greater than** `max_block_height`, but the implementation only rejects when the block height is **strictly greater than** `max_block_height`. This allows a `DelegateAction` to be executed at exactly `max_block_height`, one block beyond the user's intended expiry window.

### Finding Description
The protocol specification in `docs/RuntimeSpec/Actions.md` states:

> "If the current block is **equal or greater than** `max_block_height`" → `DelegateActionExpired` [1](#0-0) 

The `DelegateAction` struct documents `max_block_height` as "The maximal height of the block in the blockchain **below which** the given DelegateAction is valid" — meaning the action is valid only for blocks with height strictly less than `max_block_height`. [2](#0-1) 

The same "below which" semantics apply to `DelegateActionV2`: [3](#0-2) 

However, the actual enforcement in `apply_delegate_action` uses a strict `>`:

```rust
if apply_state.block_height > delegate_action.max_block_height() {
    result.result = Err(ActionErrorKind::DelegateActionExpired.into());
    return Ok(());
}
``` [4](#0-3) 

This means when `block_height == max_block_height`, the condition is `false` and the action proceeds to execution — directly contradicting the spec's "equal or greater than" language. Both `Action::Delegate` and `Action::DelegateV2` are routed through this same function: [5](#0-4) 

The existing test `test_delegate_action_max_height` only tests `max_block_height + 1` (strictly greater), never the boundary case `max_block_height == block_height`: [6](#0-5) 

### Impact Explanation
A malicious relayer who receives a signed `DelegateAction` with `max_block_height = N` can deliberately delay submission until block `N` is being processed. At that point, the check `block_height > N` evaluates to `false`, the action is not rejected, and a new receipt is generated and queued for execution — actions that the user intended to be invalid at that block height are executed. The corrupted protocol value is the receipt generated and the resulting state changes (balance transfers, function calls, etc.) that execute at a block height the user explicitly designated as the expiry boundary.

### Likelihood Explanation
Any relayer operating a meta-transaction service can observe the current chain height and time their submission to land at exactly `max_block_height`. This requires no special privileges — only the ability to submit a transaction via the public RPC. The one-block window is narrow but deterministic and exploitable by a motivated adversary. Users who set `max_block_height` to coincide with a time-sensitive protocol event (e.g., an auction close, a governance vote deadline) are most at risk.

### Recommendation
Change the strict `>` to `>=` in `apply_delegate_action` to match the protocol specification:

```rust
// Before (incorrect — allows execution at max_block_height):
if apply_state.block_height > delegate_action.max_block_height() {

// After (correct — rejects at max_block_height per spec):
if apply_state.block_height >= delegate_action.max_block_height() {
```

Update `test_delegate_action_max_height` to also assert that execution at exactly `max_block_height` returns `DelegateActionExpired`, and add a boundary test confirming that `max_block_height - 1` succeeds.

### Proof of Concept
1. User creates and signs a `DelegateAction` with `max_block_height = 1000`, intending it to be invalid at block 1000.
2. User hands the signed action to a relayer.
3. Malicious relayer waits until the chain reaches block 1000.
4. Relayer submits the transaction at block 1000.
5. `apply_delegate_action` evaluates `1000 > 1000` → `false` → no expiry error.
6. A receipt is generated and the inner actions execute at block 1000, one block beyond the user's intended expiry.
7. The user's intent (and the protocol spec) is violated: the action executes when it should have been rejected.

### Citations

**File:** docs/RuntimeSpec/Actions.md (L402-407)
```markdown
- If the current block is equal or greater than `max_block_height`

```rust
/// Delegate action has expired
DelegateActionExpired
```
```

**File:** core/primitives/src/action/delegate.rs (L59-63)
```rust
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
```

**File:** core/primitives/src/action/delegate.rs (L129-131)
```rust
    /// The maximal height of the block in the blockchain below which the given DelegateActionV2 is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
```

**File:** runtime/runtime/src/actions.rs (L435-438)
```rust
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L1358-1374)
```rust
        // Setup current block as higher than max_block_height. Must fail.
        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height + 1);
        let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);

        apply_delegate_action(
            &mut state_update,
            &apply_state,
            &VersionedActionReceipt::from(action_receipt),
            &sender_id,
            (&signed_delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");

        assert_eq!(result.result, Err(ActionErrorKind::DelegateActionExpired.into()));
    }
```

**File:** runtime/runtime/src/lib.rs (L725-746)
```rust
            Action::Delegate(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
            }
            Action::DelegateV2(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
            }
```
