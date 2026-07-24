### Title
Sub-Precision Amount Accepted by `init_transfer` Causes Permanent Fund Lock in `sign_transfer` — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`init_transfer` on NEAR accepts any amount satisfying `fee < amount` without verifying that the amount survives decimal normalization to the destination chain. When a user bridges a sub-precision amount (e.g., 1 yoctoNEAR for a token whose `origin_decimals=24, decimals=18`), the transfer is durably stored in `pending_transfers`. Every subsequent `sign_transfer` call normalizes the amount to zero and panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`, leaving the transfer permanently unclaimable and the user's tokens irreversibly locked.

---

### Finding Description

`init_transfer` (called via `ft_transfer_call` → `ft_on_transfer`) stores a `TransferMessage` after only checking `fee < amount`: [1](#0-0) 

There is no check that `normalize_amount(amount - fee, decimals) > 0`. For a token registered with `origin_decimals=24` (NEAR) and `decimals=18` (EVM), the divisor is `10^6`. Any `amount_without_fee` in the range `[1, 999_999]` yoctoNEAR normalizes to zero via floor division: [2](#0-1) 

The transfer is then stored durably in `pending_transfers`: [3](#0-2) 

When a trusted relayer later calls `sign_transfer`, `normalize_amount` is applied and the zero-check fires: [4](#0-3) 

This panic reverts the `sign_transfer` call entirely, leaving the transfer message in `pending_transfers`. There is no user-callable cancellation path and no DAO function to forcibly remove a stuck pending transfer. The transfer can never be signed, and the locked tokens can never be recovered.

---

### Impact Explanation

**Irreversible fund lock.** The user's tokens are burned/locked in `init_transfer_internal`: [5](#0-4) 

Because `sign_transfer` always panics for this transfer (the normalized amount is permanently zero), the MPC signing never occurs, no `FinTransfer` event is ever emitted on the destination chain, and no `claim_fee` path exists to recover the principal. The funds are permanently unclaimable.

---

### Likelihood Explanation

Any unprivileged user can trigger this by calling `ft_transfer_call` with a sub-precision amount on any token whose `origin_decimals > decimals` (the standard NEAR→EVM configuration uses `origin_decimals=24, decimals=18`, giving a threshold of 10^6 yoctoNEAR ≈ 0.000001 NEAR). The `ft_transfer_call` entry point is fully public. No special role or leaked key is required. The amount lost per incident is small, but the lock is permanent and the pattern is repeatable across any number of accounts.

---

### Recommendation

Add a normalization guard inside `init_transfer` (or `init_transfer_internal`) before storing the transfer message:

```rust
let normalized = Self::normalize_amount(
    transfer_message.fee.fee.0.saturating_sub(transfer_message.amount.0),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

Alternatively, reject the transfer at the `ft_on_transfer` boundary if the post-normalization amount is zero, so the tokens are immediately returned to the sender rather than locked.

---

### Proof of Concept

1. A token is registered with `origin_decimals=24, decimals=18` (standard NEAR→EVM).
2. User calls `ft_transfer_call` on the token contract with `amount=500_000` (500,000 yoctoNEAR, below the 10^6 threshold) and `msg = InitTransfer { fee: 0, native_token_fee: 0, recipient: <eth_address>, ... }`.
3. `init_transfer` passes the `fee < amount` check (0 < 500_000). The transfer is stored in `pending_transfers`. The 500,000 yoctoNEAR are burned/locked.
4. Relayer calls `sign_transfer` for this transfer.
5. `normalize_amount(500_000, Decimals{decimals:18, origin_decimals:24})` = `500_000 / 1_000_000` = `0`.
6. `require!(0 > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. The transfer remains in `pending_transfers` forever. The user's tokens are permanently locked. [4](#0-3) [2](#0-1)

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

**File:** near/omni-bridge/src/lib.rs (L1855-1866)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L2185-2196)
```rust
    fn add_transfer_message(
        &mut self,
        transfer_message: TransferMessage,
        message_owner: AccountId,
    ) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.insert_raw_transfer(transfer_message, message_owner,)
                .is_none(),
            BridgeError::KeyExists.as_ref()
        );
        env::storage_byte_cost().saturating_mul((env::storage_usage() - storage_usage).into())
```

**File:** near/omni-bridge/src/lib.rs (L2789-2792)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
