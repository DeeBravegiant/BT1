### Title
Unverified Fire-and-Forget `burn_tokens_if_needed` Allows Silent Burn Failure, Creating Unbacked Wrapped Token Supply — (File: `near/omni-bridge/src/lib.rs`)

### Summary

`burn_tokens_if_needed` schedules a cross-contract burn of deployed (wrapped) NEAR tokens using `.detach()`, meaning the bridge never awaits or verifies the result. If the burn promise fails silently (e.g., due to insufficient `BURN_TOKEN_GAS` allocation), the bridge has already committed its state changes — the pending transfer is recorded and the `InitTransferEvent` is emitted. Relayers will finalize the transfer on the destination chain and mint new tokens there, while the original wrapped tokens remain unburned in the bridge contract. This breaks the backing invariant and enables unbacked supply creation.

### Finding Description

`burn_tokens_if_needed` is defined as:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)
            .burn(amount)
            .detach();   // ← result never checked
    }
}
``` [1](#0-0) 

It is called in `init_transfer_internal` **after** the transfer message is already stored and the `InitTransferEvent` is already emitted:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
// ...
env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
U128(0)
``` [2](#0-1) 

The same unverified pattern is used in `fast_fin_transfer_to_other_chain` and `fin_transfer_send_tokens_callback` (refund path): [3](#0-2) [4](#0-3) 

The `burn` function on the deployed token contract (`OmniToken`) performs a real supply reduction:

```rust
fn burn(&mut self, amount: U128) {
    self.assert_controller();
    self.token.internal_withdraw(&env::predecessor_account_id(), amount.into());
}
``` [5](#0-4) 

If the detached promise fails (e.g., `BURN_TOKEN_GAS` is too low for the token contract's execution, or the token contract panics for any reason), the bridge has no mechanism to detect or revert this failure. The transfer proceeds to finalization on the destination chain regardless.

### Impact Explanation

When a user bridges a deployed (wrapped) NEAR token to another chain:

1. User calls `ft_transfer_call` → bridge receives tokens via `ft_on_transfer`
2. `init_transfer_internal` records the pending transfer and emits `InitTransferEvent`
3. `burn_tokens_if_needed` fires a detached burn promise — if it fails, the bridge is unaware
4. Relayers observe the event and call `fin_transfer` on the destination chain, minting new tokens there
5. The original wrapped tokens remain unburned in the bridge contract

When the user later redeems the destination-chain tokens back to NEAR, `process_fin_transfer_to_near` mints **new** tokens to the recipient for deployed tokens. The bridge now holds the original unburned tokens AND has minted a fresh set — total supply on NEAR is doubled relative to what was bridged. This is an unauthorized creation of unbacked wrapped bridge assets, matching the Critical impact tier.

### Likelihood Explanation

The entry path (`ft_transfer_call` on any deployed bridge token) is fully public and unprivileged. The burn failure is not directly attacker-controlled, but is a realistic latent condition: if `BURN_TOKEN_GAS` is set below the actual gas cost of `OmniToken::burn` (which includes a storage write), every single outbound transfer of a deployed token silently skips the burn. Gas constant misconfiguration is a common operational error, and the `.detach()` design provides no safety net. Likelihood is **Medium** — not on-demand exploitable, but a single misconfiguration makes it systemic.

### Recommendation

Replace the fire-and-forget pattern with an awaited callback that verifies burn success before committing the transfer state. Specifically:

1. In `init_transfer_internal`, schedule the burn as an awaited promise and only store the transfer message and emit the event in the success callback.
2. If the burn fails, revert the transfer message storage and return the tokens to the sender (return `transfer_message.amount` from `ft_on_transfer`).
3. Apply the same fix to `fast_fin_transfer_to_other_chain` and the refund path in `fin_transfer_send_tokens_callback`.

### Proof of Concept

1. Deploy a wrapped bridge token registered in the bridge's `deployed_tokens` set.
2. Call `ft_transfer_call(receiver=bridge, amount=100, msg=InitTransfer{recipient=<EVM address>,...})` with gas tuned so the main execution succeeds but `BURN_TOKEN_GAS` is insufficient for `OmniToken::burn`'s storage write.
3. Observe: `InitTransferEvent` is emitted, transfer is recorded in `pending_transfers`, but the bridge's token balance is unchanged (burn silently failed).
4. Relayer calls `fin_transfer` on the EVM chain — 100 tokens are minted to the EVM recipient.
5. EVM recipient calls `initTransfer` back to NEAR — relayer calls NEAR `fin_transfer`, which calls `send_tokens` → mints 100 new NEAR tokens to the recipient.
6. Result: 100 tokens exist on EVM (redeemed), 100 new tokens exist on NEAR (minted by step 5), and 100 original tokens sit unburned in the bridge contract — 200 tokens in circulation backed by 100. [6](#0-5) [1](#0-0)

### Citations

**File:** near/omni-bridge/src/lib.rs (L934-937)
```rust
            .near_expect(BridgeError::InvalidFee);

        self.burn_tokens_if_needed(fast_transfer.token_id.clone(), amount_without_fee.into());

```

**File:** near/omni-bridge/src/lib.rs (L1707-1715)
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
```

**File:** near/omni-bridge/src/lib.rs (L1811-1818)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1834-1870)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
    }
```

**File:** near/omni-token/src/lib.rs (L146-151)
```rust
    fn burn(&mut self, amount: U128) {
        self.assert_controller();

        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
    }
```
