### Title
Outgoing NEAR→EVM Transfer with Sub-Unit Net Amount Is Permanently Locked — (`near/omni-bridge/src/lib.rs`)

### Summary
`init_transfer` on NEAR accepts any transfer where `fee < amount`, including amounts where `amount − fee` is below the decimal-normalization unit. However, `sign_transfer` enforces `normalize_amount(amount − fee) > 0`. For tokens whose NEAR representation has more decimals than the EVM representation (e.g., wNEAR: 24 vs 18), any transfer where `amount − fee < 10^6` yoctoNEAR passes initiation but permanently fails signing. No cancel or refund path exists for pending transfers, so the user's tokens are irreversibly locked.

### Finding Description

**Initiation gate — no minimum net-amount check:**

`init_transfer` (reached via `ft_transfer_call`) only validates `fee < amount`: [1](#0-0) 

Any positive net amount, including 1 yoctoNEAR, is accepted and the tokens are immediately locked in the bridge.

**Signing gate — enforces `normalize_amount > 0`:**

`sign_transfer` computes the destination-chain amount via floor division and hard-reverts if the result is zero: [2](#0-1) 

**Normalization arithmetic:**

For a token with `origin_decimals = 24` (NEAR) and `decimals = 18` (EVM, capped by `_normalizeDecimals`), the divisor is `10^6`: [3](#0-2) 

Any `amount − fee < 10^6` yoctoNEAR normalizes to 0, causing `sign_transfer` to revert with `ERR_INVALID_AMOUNT_TO_TRANSFER` on every call — permanently.

**No recovery path:**

The only function that removes a transfer from `pending_transfers` is `claim_fee_callback`, which requires a valid `FinTransfer` proof from the destination chain: [4](#0-3) 

Because `sign_transfer` always fails, no MPC signature is ever produced, no `finTransfer` is ever submitted on EVM, and no `FinTransfer` proof can ever be generated. There is no `cancel_transfer` or user-accessible refund function. The transfer and its locked tokens are permanently stranded.

### Impact Explanation

Matches **Critical — Irreversible fund lock / permanently unclaimable user value in bridge flows**.

A user who initiates a NEAR→EVM transfer with a net amount below `10^6` yoctoNEAR (for a 24-decimal NEAR token) has their tokens locked in the bridge contract with no on-chain recovery path. The `pending_transfers` entry persists indefinitely, and the locked token balance is never released.

### Likelihood Explanation

**Low.** The threshold is `10^6` yoctoNEAR ≈ 0.000001 NEAR, an extremely small amount. Ordinary users are unlikely to bridge such dust amounts intentionally. However:
- The protocol provides no warning or minimum-amount guard at initiation time.
- The `sign_transfer` guard was clearly added deliberately (it has its own error code `ERR_INVALID_AMOUNT_TO_TRANSFER`), yet no corresponding guard was added at `init_transfer`.
- Any user who does trigger this — accidentally or through a buggy integration — has no recourse.

### Recommendation

Add a minimum net-amount check inside `init_transfer` (or its internal handler) that mirrors the `sign_transfer` guard:

```rust
// In init_transfer, after computing transfer_message:
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee()
        .near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(amount_to_transfer > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

Alternatively, add a `cancel_transfer` function that allows the original sender to reclaim locked tokens for transfers that have never been signed.

### Proof of Concept

1. Token: wNEAR, `origin_decimals = 24`, `decimals = 18` (EVM). Divisor = `10^6`.
2. User calls `ft_transfer_call` with `amount = 5` yoctoNEAR, `fee = 0`, recipient = EVM address.
3. `init_transfer` passes: `fee (0) < amount (5)`. Tokens locked. Transfer stored in `pending_transfers`.
4. Relayer calls `sign_transfer`:
   - `amount_without_fee() = 5`
   - `normalize_amount(5, {decimals:18, origin_decimals:24}) = 5 / 10^6 = 0`
   - `require!(0 > 0, ...)` → **panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`**
5. Step 4 repeats on every call — no MPC signature is ever produced.
6. No `finTransfer` is submitted on EVM → no `FinTransfer` proof exists → `claim_fee_callback` is unreachable.
7. User's 5 yoctoNEAR are permanently locked.

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

**File:** near/omni-bridge/src/lib.rs (L1098-1098)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```

**File:** near/omni-bridge/src/lib.rs (L2789-2792)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
