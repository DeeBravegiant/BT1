### Title
`fin_transfer_send_tokens_callback` Silently Ignores `ft_transfer` Failure, Permanently Locking User Funds — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When `fin_transfer` delivers tokens to a NEAR recipient via `ft_transfer` (empty `msg` field), a failure of that call — e.g., the recipient is blacklisted by USDC on NEAR — is silently ignored by the callback. The transfer is permanently marked as finalized, the locked-token counter is decremented, and the user's tokens are irreversibly stranded in the bridge contract with no on-chain recovery path.

---

### Finding Description

**Step 1 — State mutations before the async call.**

In `process_fin_transfer_to_near` (lib.rs:1872–1983):

- `add_fin_transfer` inserts the transfer ID into `finalised_transfers` (line 1880).
- `unlock_tokens_if_needed` decrements `locked_tokens[(origin_chain, token)]` (lines 1886–1890).
- `send_tokens` dispatches either `ft_transfer` or `ft_transfer_call` depending on whether `msg` is empty (lines 1962–1982).
- `fin_transfer_send_tokens_callback` is scheduled with `is_ft_transfer_call = !msg.is_empty()` (line 1978). [1](#0-0) [2](#0-1) 

**Step 2 — The callback does not check the promise result for `ft_transfer`.**

`is_refund_required` (lib.rs:1789–1809) only inspects the promise result when `is_ft_transfer_call` is `true`. When `is_ft_transfer_call` is `false` (i.e., `ft_transfer` was used because `msg` is empty), it unconditionally returns `false` — no promise-result check is performed. [3](#0-2) 

**Step 3 — Callback proceeds as success on failure.**

In NEAR, a failed cross-contract call still triggers the callback with a `Failed` promise result. When `ft_transfer` panics (e.g., recipient blacklisted by USDC), the callback runs, `is_refund_required(false)` returns `false`, and the `else` branch executes: fees are dispatched and a `FinTransferEvent` is emitted. The state mutations — `finalised_transfers` insertion and `locked_tokens` decrement — are never rolled back. [4](#0-3) 

**Step 4 — No admin recovery path.**

`remove_fin_transfer` (lib.rs:2327–2338) is a private helper only reachable from the refund path inside `fin_transfer_send_tokens_callback`. There is no public or admin-accessible function to undo a finalized transfer. [5](#0-4) 

---

### Impact Explanation

The user's tokens are permanently stranded in the bridge contract. The transfer ID is in `finalised_transfers`, so any retry of `fin_transfer` reverts with `TransferAlreadyFinalised`. The `locked_tokens` counter for the origin chain is permanently decremented even though the tokens were never delivered, creating an accounting divergence between the actual bridge balance and the tracked locked amount. The user has no on-chain recourse.

**Matched impact category:** Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows.

---

### Likelihood Explanation

NEAR hosts USDC and other tokens with blacklist mechanisms. A user whose NEAR address is blacklisted after initiating a cross-chain transfer — or who sends to a NEAR address that is subsequently blacklisted before the relayer finalizes — triggers this path. The `msg` field is empty for standard (non-DeFi-routing) transfers, which is the common case. Likelihood is low-to-medium given the dependency on blacklisting, but the consequence is total and irreversible.

---

### Recommendation

In `is_refund_required`, also check the promise result when `is_ft_transfer_call` is `false`:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        // existing logic unchanged
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => {
                if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                    amount.0 == 0
                } else {
                    false
                }
            }
            Err(_) => false,
        }
    } else {
        // ft_transfer: treat a Failed promise as requiring rollback
        env::promise_result_checked(0, 0).is_err()
    }
}
```

Additionally, add an admin-callable function to forcibly remove a finalized transfer and restore locked-token accounting, analogous to the PearVault recommendation to allow admin cancellation of stuck withdrawal requests.

---

### Proof of Concept

1. Alice initiates a USDC transfer from Ethereum to NEAR, specifying her NEAR address as recipient with an empty `msg` field (standard transfer, no DeFi routing).
2. Before the relayer calls `fin_transfer`, Alice's NEAR address is blacklisted by the USDC contract on NEAR.
3. Relayer calls `fin_transfer` → `fin_transfer_callback` → `process_fin_transfer_to_near`:
   - `add_fin_transfer` inserts `transfer_id` into `finalised_transfers`.
   - `unlock_tokens_if_needed` decrements `locked_tokens[(Eth, usdc)]`.
   - `send_tokens` dispatches `ft_transfer(alice, amount)` → USDC panics (blacklisted recipient).
4. `fin_transfer_send_tokens_callback` runs with `is_ft_transfer_call = false`:
   - `is_refund_required(false)` → `false` (no promise-result check).
   - `else` branch: fee sent to relayer, `FinTransferEvent` emitted as success.
5. Alice's USDC is permanently stranded in the bridge contract. Retrying `fin_transfer` reverts with `TransferAlreadyFinalised`. No admin function exists to recover the funds.

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

**File:** near/omni-bridge/src/lib.rs (L1880-1890)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());

        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/lib.rs (L1962-1982)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
```

**File:** near/omni-bridge/src/lib.rs (L2327-2338)
```rust
    fn remove_fin_transfer(&mut self, transfer_id: &TransferId, storage_owner: &AccountId) {
        let storage_usage = env::storage_usage();
        self.finalised_transfers.remove(transfer_id);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(storage_owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(storage_owner, &storage);
        }
    }
```
