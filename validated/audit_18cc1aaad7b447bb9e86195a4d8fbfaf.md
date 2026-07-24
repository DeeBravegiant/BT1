### Title
Missing Normalized-Amount Validation at Transfer Initiation Causes Permanent Fund Lock — (`near/omni-bridge/src/lib.rs`)

### Summary

`init_transfer` validates only that `fee < amount` in raw NEAR-side token units, but never checks that `normalize_amount(amount - fee)` is non-zero. Because `normalize_amount` uses floor division to scale amounts from NEAR's origin decimals down to the destination chain's decimals, any transfer where `(amount - fee) < 10^(origin_decimals - decimals)` will pass initiation, lock the user's tokens, and then permanently fail every subsequent `sign_transfer` call with `ERR_INVALID_AMOUNT_TO_TRANSFER`. No cancel or refund path exists for pending transfers.

### Finding Description

**Bug class (from external report):** asset/accounting — a validation check uses the raw (pre-scaling) amount to gate acceptance, while the actual operation uses a scaled amount that can floor-divide to zero, making the operation permanently unreachable after funds are committed.

**Exact root cause in Omni Bridge:**

`init_transfer` (called from `ft_on_transfer`) stores the transfer and locks the user's tokens after only one fee check:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

No normalization check is performed here. Tokens are locked and the transfer is stored in `pending_transfers`.

Later, `sign_transfer` normalizes the net amount before signing:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [2](#0-1) 

`normalize_amount` is a pure floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [3](#0-2) 

`Decimals` stores both the destination-chain precision (`decimals`) and the NEAR-side precision (`origin_decimals`), set at token registration time: [4](#0-3) 

For any token where `origin_decimals > decimals` (e.g., a 24-decimal NEAR token bridging to an 18-decimal EVM token, `diff_decimals = 6`), any transfer where `amount - fee < 10^6` normalizes to zero. The `require!(amount_to_transfer > 0)` in `sign_transfer` will always panic, but the tokens are already locked.

There is no public cancel or refund function for pending transfers. `remove_transfer_message` is only reachable through `sign_transfer_callback` (only when fee is zero and signing succeeds), `claim_fee_callback` (requires a finalized destination-chain proof), and `fin_transfer_send_tokens_callback` (requires a successful `fin_transfer` flow). None of these are reachable when `sign_transfer` always reverts. [5](#0-4) 

### Impact Explanation

**Irreversible fund lock.** A user who initiates a NEAR-outbound transfer with `(amount - fee) < 10^(origin_decimals - decimals)` will have their tokens permanently locked in the bridge contract with no recovery path. The transfer occupies a slot in `pending_transfers` indefinitely and the locked token balance is never released.

This matches the allowed impact: *"Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows."*

### Likelihood Explanation

Requires a token registered with `origin_decimals > decimals` (realistic for any NEAR-native 24-decimal token bridging to an 18-decimal EVM chain) and a user sending a sub-unit amount below the normalization threshold. This can happen accidentally (e.g., a user sending a dust amount or a bot with a rounding error). The protocol provides no guard at the entry point.

### Recommendation

Add a normalization check inside `init_transfer` (or `init_transfer_internal`) before storing the transfer and locking tokens:

```rust
let token_address = self.get_token_address(
    init_transfer_msg.get_destination_chain(),
    token_id.clone(),
).near_expect(BridgeError::FailedToGetTokenAddress);

let decimals = self.token_decimals
    .get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);

let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

This mirrors the existing check in `sign_transfer` but gates it at initiation time, before any tokens are locked.

### Proof of Concept

1. A token is registered with `origin_decimals = 24`, `decimals = 18` (NEAR → ETH, `diff_decimals = 6`).
2. User calls `ft_transfer_call` → `ft_on_transfer` → `init_transfer` with `amount = 500_000` (raw units), `fee = 0`.
3. Check `fee (0) < amount (500_000)` passes. Tokens are locked. Transfer stored in `pending_transfers`.
4. Relayer calls `sign_transfer`.
5. `normalize_amount(500_000, {origin_decimals:24, decimals:18}) = 500_000 / 1_000_000 = 0`.
6. `require!(0 > 0, "ERR_INVALID_AMOUNT_TO_TRANSFER")` → panic.
7. User's 500,000 raw units are permanently locked. No cancel path exists.

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

**File:** near/omni-bridge/src/lib.rs (L2199-2216)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }
```

**File:** near/omni-bridge/src/lib.rs (L2729-2740)
```rust
        require!(
            self.token_decimals
                .insert(
                    token_address,
                    &Decimals {
                        decimals,
                        origin_decimals,
                    }
                )
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L2789-2792)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
