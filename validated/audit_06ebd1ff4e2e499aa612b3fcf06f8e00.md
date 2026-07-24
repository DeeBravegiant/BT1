### Title
`locked_tokens` Permanently Deflated via Fee Unlocked from Destination Chain in Fast-Transfer-to-Other-Chain Path — (`near/omni-bridge/src/lib.rs`)

### Summary

In the fast-transfer-to-other-chain flow, `fast_fin_transfer_to_other_chain` locks only `amount_without_fee` on the destination chain. However, `send_fee_internal` — called unconditionally from `claim_fee_callback` — always calls `unlock_tokens_if_needed(destination_chain, token, fee)`. Because the fee was never locked on the destination chain in this path, each fast-transfer + fee-claim cycle permanently deflates `locked_tokens[destination]` by `fee`. Over time this causes legitimate redemptions from the destination chain to revert with `InsufficientLockedTokens`, permanently freezing user funds.

### Finding Description

**Bug class:** Asset/accounting — balance-accounting divergence between the lock phase and the unlock phase, analogous to H-11's asymmetric protocol-fee handling between loan creation and liquidation.

**Normal (non-fast) transfer to other chain** — `process_fin_transfer_to_other_chain`:

```
unlock_tokens_if_needed(origin_chain, token, full_amount)
lock_tokens_if_needed(destination_chain, token, fee)          // fee portion
lock_tokens_if_needed(destination_chain, token, amount_without_fee)  // principal
// → locked_tokens[destination] += full_amount
```

Later, `claim_fee_callback` → `send_fee_internal`:

```
unlock_tokens_if_needed(destination_chain, token, fee)
// → locked_tokens[destination] -= fee  (correct: fee was locked above)
```

**Fast transfer to other chain** — `fast_fin_transfer_to_other_chain`:

```rust
self.lock_tokens_if_needed(
    fast_transfer.get_destination_chain(),
    &fast_transfer.token_id,
    amount_without_fee,   // ← only amount_without_fee is locked
);
``` [1](#0-0) 

The fee is **never** locked on the destination chain in this path. Yet `claim_fee_callback` calls `send_fee_internal` identically to the normal path:

```rust
self.unlock_tokens_if_needed(transfer_message.get_destination_chain(), &token, token_fee);
``` [2](#0-1) 

`send_fee_internal` has no awareness of whether the transfer was a fast transfer; it always unlocks `fee` from the destination chain. [3](#0-2) 

**Net effect per cycle:**

| Step | `locked_tokens[destination]` |
|---|---|
| Before fast transfer | `V` |
| After `fast_fin_transfer_to_other_chain` | `V + amount_without_fee` |
| After `claim_fee_callback` | `V + amount_without_fee − fee` ← **should be `V + amount_without_fee`** |

Each cycle deflates `locked_tokens[destination]` by `fee`.

### Impact Explanation

`locked_tokens` is the bridge's backing-guarantee ledger. `unlock_tokens` enforces:

```rust
require!(
    available >= amount,
    TokenLockError::InsufficientLockedTokens.as_ref()
);
``` [4](#0-3) 

When a user later bridges tokens **from** the destination chain back to NEAR, `process_fin_transfer_to_near` calls:

```rust
self.unlock_tokens_if_needed(
    transfer_message.get_origin_chain(),   // = destination chain
    &token,
    transfer_message.amount.0,            // full amount
)
``` [5](#0-4) 

After accumulated deflation, `locked_tokens[destination] < full_amount` for a legitimate redemption, causing an `InsufficientLockedTokens` panic. The user's tokens are permanently frozen on the destination chain with no recovery path, because the NEAR bridge contract will always reject the redemption.

### Likelihood Explanation

The path is triggered by **normal, non-malicious operation** of any trusted relayer executing a fast transfer to a non-Near destination chain (e.g., Eth, Sol, Strk). The `fast_fin_transfer` entry point is gated by `#[trusted_relayer]`, but trusted relayers are registered protocol participants performing their expected role — no malicious intent is required. The bug accumulates silently with every fast-transfer + fee-claim cycle for any non-native token bridged to a non-Near chain.

### Recommendation

In `send_fee_internal`, skip the destination-chain unlock when the transfer originated as a fast transfer (i.e., when `transfer_message.origin_transfer_id` is `Some`), because in that path the fee was never locked on the destination chain:

```rust
// Only unlock from destination chain if this is NOT a fast transfer leg
if transfer_message.origin_transfer_id.is_none() {
    self.unlock_tokens_if_needed(
        transfer_message.get_destination_chain(),
        &token,
        token_fee,
    );
}
```

Alternatively, align `fast_fin_transfer_to_other_chain` to lock the full amount (including fee) on the destination chain, matching the normal-path invariant.

### Proof of Concept

1. Token `T` is native to Eth. `locked_tokens[(Sol, T)] = 1_000_000`.
2. Trusted relayer executes a fast transfer of `T` (amount = 100_000, fee = 1_000) to Solana:
   - `fast_fin_transfer_to_other_chain` locks `99_000` on Sol.
   - `locked_tokens[(Sol, T)] = 1_099_000`.
3. Relayer claims fee via `claim_fee`:
   - `send_fee_internal` unlocks `1_000` from Sol.
   - `locked_tokens[(Sol, T)] = 1_098_000` ← should be `1_099_000`.
4. After 1_000 such cycles: `locked_tokens[(Sol, T)] = 1_000_000 + 1_000 * 99_000 − 1_000 * 1_000 = 98_000_000`.
   Wait — actually the deflation is `1_000` per cycle, so after 1_000 cycles: `locked_tokens[(Sol, T)] = 1_000_000 + 1_000 * 99_000 − 1_000 * 1_000 = 99_000_000`. The deflation is `1_000 * 1_000 = 1_000_000` below the correct value.
5. A user who bridged `1_000_000` tokens to Solana in the normal path tries to redeem them back to NEAR. `process_fin_transfer_to_near` calls `unlock_tokens_if_needed(Sol, T, 1_000_000)`. If `locked_tokens[(Sol, T)]` has been deflated below `1_000_000`, the call panics with `InsufficientLockedTokens` and the user's redemption is permanently blocked. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L918-942)
```rust
    fn fast_fin_transfer_to_other_chain(
        &mut self,
        fast_transfer: &FastTransfer,
        storage_payer: AccountId,
        relayer_id: AccountId,
    ) {
        if fast_transfer.recipient.is_utxo_chain() {
            let btc_account_id = self.get_utxo_chain_token(fast_transfer.get_destination_chain());
            require!(
                fast_transfer.token_id == btc_account_id,
                BridgeError::NativeTokenRequiredForChain.as_ref()
            );
        }

        let amount_without_fee = fast_transfer
            .amount_without_fee()
            .near_expect(BridgeError::InvalidFee);

        self.burn_tokens_if_needed(fast_transfer.token_id.clone(), amount_without_fee.into());

        self.lock_tokens_if_needed(
            fast_transfer.get_destination_chain(),
            &fast_transfer.token_id,
            amount_without_fee,
        );
```

**File:** near/omni-bridge/src/lib.rs (L1886-1890)
```rust
        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/lib.rs (L2655-2707)
```rust
    fn send_fee_internal(
        &mut self,
        transfer_message: &TransferMessage,
        fee_recipient: AccountId,
        token_fee: u128,
    ) -> PromiseOrValue<()> {
        if transfer_message.fee.native_fee.0 != 0 {
            let origin_chain = transfer_message.origin_transfer_id.as_ref().map_or_else(
                || transfer_message.get_origin_chain(),
                |origin_transfer_id| origin_transfer_id.origin_chain,
            );

            if origin_chain.is_utxo_chain() {
                env::panic_str(BridgeError::NativeFeeForUtxoChain.to_string().as_str())
            } else if origin_chain == ChainKind::Near {
                Promise::new(fee_recipient.clone())
                    .transfer(NearToken::from_yoctonear(transfer_message.fee.native_fee.0))
                    .detach();
            } else {
                ext_token::ext(self.get_native_token_id(origin_chain))
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
        }

        let token = self.get_token_id(&transfer_message.token);
        env::log_str(
            &OmniBridgeEvent::ClaimFeeEvent {
                transfer_message: transfer_message.clone(),
            }
            .to_log_string(),
        );

        self.unlock_tokens_if_needed(transfer_message.get_destination_chain(), &token, token_fee);

        if token_fee > 0 {
            if self.is_deployed_token(&token) {
                ext_token::ext(token)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient, U128(token_fee), None)
                    .into()
            } else {
                ext_token::ext(token)
                    .with_static_gas(FT_TRANSFER_GAS)
                    .with_attached_deposit(ONE_YOCTO)
                    .ft_transfer(fee_recipient, U128(token_fee), None)
                    .into()
            }
        } else {
            PromiseOrValue::Value(())
        }
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
