### Title
Sub-Threshold Amount Normalizes to Zero After Token Burn/Lock, Permanently Trapping User Funds — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a NEAR user initiates an outbound transfer with an amount smaller than the decimal-normalization divisor (`10^(origin_decimals − decimals)`), the bridge burns or locks the tokens in `init_transfer_internal` and returns `U128(0)` to the NEP-141 caller (consuming all tokens). Later, when a relayer calls `sign_transfer`, `normalize_amount` produces zero and the function panics with `InvalidAmountToTransfer`. No public cancellation path exists, so the transfer message stays in `pending_transfers` forever and the user's tokens are irrecoverably lost.

---

### Finding Description

**Step 1 — Token destruction before validation.**

`init_transfer_internal` burns deployed bridge tokens and locks native tokens, then emits `InitTransferEvent` and returns `U128(0)`:

```
near/omni-bridge/src/lib.rs  lines 1855-1869
```

The NEP-141 protocol interprets `U128(0)` as "all tokens accepted"; no refund is issued to the sender.

**Step 2 — Zero-amount check fires too late.**

`sign_transfer` (called later by a relayer) applies `normalize_amount`:

```
near/omni-bridge/src/lib.rs  lines 479-489
normalize_amount = amount / 10^(origin_decimals − decimals)
require!(amount_to_transfer > 0, InvalidAmountToTransfer)
```

`normalize_amount` uses floor division:

```
near/omni-bridge/src/lib.rs  lines 2789-2791
amount / (10_u128.pow(diff_decimals))
```

For any `amount < 10^(origin_decimals − decimals)` the result is `0`, and `sign_transfer` panics unconditionally.

**Step 3 — No recovery path.**

`remove_transfer_message` is only called inside `sign_transfer_callback` (on successful MPC signing) and `claim_fee_callback`. Because `sign_transfer` panics before reaching the MPC call, neither callback fires. There is no public `cancel_transfer` or emergency-refund function. The entry in `pending_transfers` is permanent, and the burned/locked tokens are unrecoverable.

**Concrete decimal scenario.**

A token registered with `origin_decimals = 24` (NEAR yocto) and `decimals = 18` (EVM representation) has a divisor of `10^6`. Any transfer of fewer than `1 000 000` yoctoNEAR (i.e., less than `0.000001 NEAR`) normalizes to zero. The bridge accepts the tokens, burns/locks them, and then permanently rejects every subsequent `sign_transfer` call for that transfer ID.

---

### Impact Explanation

**Critical — Irreversible fund lock / permanent burn.**

- For deployed bridge tokens: `burn_tokens_if_needed` destroys them on-chain; they cannot be re-minted without a successful `fin_transfer` on the destination chain, which requires a valid MPC signature, which requires `sign_transfer` to succeed — an impossible condition.
- For non-deployed (native) tokens: `lock_tokens_if_needed` increments `locked_tokens`; the only unlock path is a `fin_transfer` proof from the destination chain, which again requires a completed MPC signing round that can never happen.

In both cases the user's funds are permanently unclaimable.

---

### Likelihood Explanation

**Medium.**

- Any user who sends a "dust" or test amount below the normalization threshold triggers the bug. For tokens with large decimal gaps (e.g., 24 → 18, 18 → 6) the threshold is non-trivial (up to `10^12` base units for an 18→6 token).
- The protocol provides no on-chain warning, minimum-amount guard, or simulation tool at `init_transfer` time.
- The `ft_transfer_call` interface is the standard NEP-141 entry point; any wallet or script that does not pre-compute the normalization threshold can trigger this accidentally.
- The SECURITY.md comment acknowledges only "dust remainder" locking, not the total-amount-to-zero case, so the risk is not surfaced to integrators.

---

### Recommendation

Add a normalization check **before** burning or locking tokens. The earliest safe point is inside `init_transfer` (or `ft_on_transfer`), after the `Decimals` record is fetched:

```rust
let decimals = self.token_decimals.get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
let normalized = Self::normalize_amount(
    amount_without_fee,
    decimals,
);
require!(normalized > 0, BridgeError::AmountTooSmallToNormalize);
```

This ensures the transfer is rejected (and the NEP-141 refund is issued) before any irreversible state change occurs.

---

### Proof of Concept

1. Register a token with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6`).
2. Call `ft_transfer_call` on the NEAR token contract with `amount = 500_000` (< `10^6`) and a valid EVM recipient.
3. `ft_on_transfer` → `init_transfer` → `init_transfer_internal`:
   - `burn_tokens_if_needed` destroys 500 000 units (deployed token) **or** `lock_tokens_if_needed` locks them (native token).
   - Returns `U128(0)` → NEP-141 issues no refund.
4. Relayer calls `sign_transfer(transfer_id, ...)`.
5. `normalize_amount(500_000, {origin: 24, dest: 18})` = `500_000 / 1_000_000` = `0`.
6. `require!(0 > 0, InvalidAmountToTransfer)` → **panic**.
7. No MPC call is made; `sign_transfer_callback` never fires; `pending_transfers` retains the entry indefinitely.
8. User's 500 000 units are permanently burned/locked with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** near/omni-bridge/src/lib.rs (L652-671)
```rust
    #[private]
    pub fn sign_transfer_callback(
        &mut self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] message_payload: TransferMessagePayload,
        #[serializer(borsh)] fee: &Fee,
    ) {
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }

            env::log_str(
                &OmniBridgeEvent::SignTransferEvent {
                    signature,
                    message_payload,
                }
                .to_log_string(),
            );
        }
```

**File:** near/omni-bridge/src/lib.rs (L1855-1869)
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

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
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
