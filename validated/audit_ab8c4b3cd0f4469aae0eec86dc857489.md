### Title
Irreversible Fund Lock via Rounding-to-Zero in `normalize_amount` After Tokens Are Already Burned/Locked in `init_transfer` - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

The NEAR bridge contract burns or locks a user's tokens during `init_transfer_internal`, but only validates that the normalized destination amount is non-zero much later in `sign_transfer`. For tokens whose origin chain has more decimals than the NEAR representation, a small-but-nonzero transfer amount can normalize to zero via floor division. Once tokens are burned/locked, `sign_transfer` will always revert with `InvalidAmountToTransfer`, leaving the transfer permanently stuck with no cancellation or refund path.

---

### Finding Description

`normalize_amount` performs floor integer division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

For any token where `origin_decimals > decimals` (e.g., 18-decimal ETH token mapped to 6-decimal NEAR representation, `diff_decimals = 12`), any transfer amount below `10^12` in the smallest unit normalizes to zero.

The `init_transfer` function only validates `fee.fee < amount`:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [2](#0-1) 

It then proceeds to `init_transfer_internal`, which **irreversibly burns or locks the tokens**:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
``` [3](#0-2) 

Only later, when a relayer calls `sign_transfer`, does the zero-amount check occur:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [4](#0-3) 

Since `sign_transfer` is the only mechanism to produce the MPC signature required for destination-chain finalization, and it will always revert for this transfer, the transfer is permanently unresolvable. No public cancellation or refund function exists in the contract.

---

### Impact Explanation

**Critical — Irreversible fund lock.** A user's tokens are burned (for bridged/deployed tokens) or locked (for native tokens) in `init_transfer_internal`. Because `sign_transfer` will always revert with `InvalidAmountToTransfer` for this transfer, no MPC signature is ever produced, the destination chain can never finalize the transfer, and there is no on-chain path to recover the funds. The transfer message persists in storage but is permanently unclaimable.

---

### Likelihood Explanation

**Medium.** The condition requires a token registered with `origin_decimals > decimals` (a common configuration — e.g., an 18-decimal EVM token normalized to 6 or 8 decimals on NEAR) and a transfer amount below the normalization threshold. Any unprivileged user calling `ft_transfer_call` with a small amount triggers the path. No special role or key is needed. The user may do this accidentally (e.g., sending dust amounts or amounts just above the fee threshold).

---

### Recommendation

Add the normalized-amount zero-check inside `init_transfer` **before** tokens are burned or locked, mirroring the guard already present in `sign_transfer`:

```rust
// In init_transfer, after constructing transfer_message:
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
);
if let Some(token_address) = token_address {
    if let Some(decimals) = self.token_decimals.get(&token_address) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee().unwrap_or(0),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This ensures the transaction reverts before any state mutation, returning the tokens to the user via the `ft_transfer_call` refund mechanism (returning the full amount from `ft_on_transfer`).

---

### Proof of Concept

1. A token is registered with `origin_decimals = 18`, `decimals = 6` (diff = 12, divisor = `10^12`).
2. User calls `ft_transfer_call` with `amount = 500_000_000_000` (500 billion, i.e., 0.0005 in 6-decimal terms) and `fee = 0`.
3. `init_transfer` passes the `fee < amount` check (0 < 500_000_000_000).
4. `init_transfer_internal` burns or locks `500_000_000_000` of the user's tokens.
5. Relayer calls `sign_transfer`. `normalize_amount(500_000_000_000, {18, 6})` = `500_000_000_000 / 10^12` = `0`.
6. `require!(amount_to_transfer > 0, ...)` panics — `sign_transfer` always reverts.
7. No MPC signature is ever produced. The transfer message is stuck in storage. The user's `500_000_000_000` tokens are permanently lost. [5](#0-4) [6](#0-5) [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L479-489)
```rust
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );

        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L558-561)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
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

**File:** near/omni-bridge/src/lib.rs (L2786-2792)
```rust
    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
