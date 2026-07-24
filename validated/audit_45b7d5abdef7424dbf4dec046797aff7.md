### Title
Partial `ft_transfer_call` refund silently understates `locked_tokens`, permanently freezing funds for other users - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`fin_transfer_send_tokens_callback` treats any non-zero result from `ft_resolve_transfer` as full success. When a recipient's `ft_on_transfer` returns a partial unused amount, the token contract refunds those tokens to the bridge, but the bridge never re-increments `locked_tokens` for the undelivered portion. Because `locked_tokens` is decremented by the full requested amount before the transfer, the counter becomes permanently understated, causing all future `fin_transfer` calls for that token to revert with `InsufficientLockedTokens`, freezing other users' funds.

---

### Finding Description

**Entry path**: An unprivileged attacker deploys a contract that implements `ft_on_transfer` to return a non-zero partial amount (e.g., returns 499 out of 999 tokens). The attacker initiates a legitimate cross-chain transfer from ETH to NEAR with their contract as the recipient and a non-empty `msg`, triggering the `ft_transfer_call` path in `send_tokens`.

**Step 1 — `locked_tokens` is decremented by the full amount before delivery.**

In `process_fin_transfer_to_near`, `unlock_tokens_if_needed` is called unconditionally before `send_tokens`:

```rust
let lock_actions = vec![self.unlock_tokens_if_needed(
    transfer_message.get_origin_chain(),
    &token,
    transfer_message.amount.0,
)];
``` [1](#0-0) 

`unlock_tokens` panics if `available < amount`, so the counter is decremented by the full amount at this point. [2](#0-1) 

**Step 2 — `send_tokens` uses `ft_transfer_call` when `msg` is non-empty.**

For non-deployed tokens with a non-empty `msg`, `send_tokens` calls `ft_transfer_call`:

```rust
ext_token::ext(token)
    .with_attached_deposit(ONE_YOCTO)
    .with_static_gas(ft_transfer_call_gas)
    .ft_transfer_call(recipient, amount, None, msg.to_string())
``` [3](#0-2) 

For deployed tokens with a non-empty `msg`, `send_tokens` calls `mint(..., Some(msg))`, which internally mints to the bridge and calls `ft_transfer_call`. [4](#0-3) 

**Step 3 — `is_refund_required` only triggers on a zero result, ignoring partial refunds.**

The callback reads the `ft_resolve_transfer` result (amount used) and only triggers a revert if it is exactly zero:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => {
                if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                    amount.0 == 0   // ← only full-refund triggers revert
                } else { false }
            }
            Err(_) => false,
        }
    } else { false }
}
``` [5](#0-4) 

When the attacker's `ft_on_transfer` returns 499 (partial refund), `ft_resolve_transfer` returns 500 (amount used). `is_refund_required` returns `false`.

**Step 4 — `fin_transfer_send_tokens_callback` does not revert lock actions for partial refunds.**

```rust
if Self::is_refund_required(is_ft_transfer_call) {
    self.burn_tokens_if_needed(...);
    self.revert_lock_actions(&lock_actions);   // ← only called on full refund
    self.remove_fin_transfer(...);
    ...
} else {
    // send fee, log event — no re-locking of undelivered portion
}
``` [6](#0-5) 

`revert_lock_actions` is never called for the partial case, so `locked_tokens` remains decremented by the full amount even though only a partial amount was delivered. [7](#0-6) 

**Step 5 — Future `fin_transfer` calls for the same token revert.**

Any subsequent `fin_transfer` for the same deployed token

### Citations

**File:** near/omni-bridge/src/lib.rs (L1707-1751)
```rust
        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
                token.clone(),
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
            );

            self.revert_lock_actions(&lock_actions);

            self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);

            env::log_str(
                &OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string(),
            );
        } else {
            // Send fee to the fee recipient
            if transfer_message.fee.fee.0 > 0 {
                if self.is_deployed_token(&token) {
                    ext_token::ext(token)
                        .with_static_gas(MINT_TOKEN_GAS)
                        .mint(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                } else {
                    ext_token::ext(token)
                        .with_attached_deposit(ONE_YOCTO)
                        .with_static_gas(FT_TRANSFER_GAS)
                        .ft_transfer(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                }
            }

            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }

            env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
        }
```

**File:** near/omni-bridge/src/lib.rs (L1789-1809)
```rust
    fn is_refund_required(is_ft_transfer_call: bool) -> bool {
        if is_ft_transfer_call {
            match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
                Ok(value) => {
                    if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                        // Normal case: refund if the used token amount is zero
                        // The amount can be zero if the `ft_on_transfer` in the receiver contract returns an amount instead of `0`, or if it panics.
                        amount.0 == 0
                    } else {
                        // Unexpected case: don't refund
                        false
                    }
                }
                // Unexpected case: don't refund
                Err(_) => false,
            }
        } else {
            // Not ft_transfer_call: don't refund
            false
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1886-1890)
```rust
        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/lib.rs (L2099-2106)
```rust
            ext_token::ext(token)
                .with_attached_deposit(deposit)
                .with_static_gas(MINT_TOKEN_GAS.saturating_add(ft_transfer_call_gas))
                .mint(
                    recipient,
                    amount,
                    (!msg.is_empty()).then(|| msg.to_string()),
                )
```

**File:** near/omni-bridge/src/lib.rs (L2118-2122)
```rust
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(ft_transfer_call_gas)
                .ft_transfer_call(recipient, amount, None, msg.to_string())
        }
```

**File:** near/omni-bridge/src/token_lock.rs (L71-94)
```rust
    fn unlock_tokens(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        let key = (chain_kind, token_id.clone());
        let Some(available) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
        require!(
            available >= amount,
            TokenLockError::InsufficientLockedTokens.as_ref()
        );

        let remaining = available - amount;
        self.locked_tokens.insert(&key, &remaining);

        LockAction::Unlocked {
            chain_kind,
            token_id: token_id.clone(),
            amount,
        }
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L122-142)
```rust
    pub fn revert_lock_actions(&mut self, lock_actions: &[LockAction]) {
        for lock_action in lock_actions {
            match lock_action {
                LockAction::Locked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.unlock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unlocked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.lock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unchanged => {}
            }
        }
    }
```
