### Title
Fee Rounding to Zero Allows Deposits Without Paying Protocol Fees - (`contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary

All L2 deposit pool contracts compute the swap fee using plain integer division: `fee = amount * feeBps / 10_000`. When `amount * feeBps < 10_000`, the result truncates to zero. Because no minimum deposit amount is enforced in any pool contract, an attacker can call `deposit()` repeatedly with dust amounts to accumulate a large wrsETH/rsETH position while paying zero protocol fees. The protocol permanently loses the fee revenue that should have been collected.

---

### Finding Description

Every L2 pool contract computes the fee identically:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

When `amount * feeBps < 10_000`, Solidity integer division truncates `fee` to `0`, so `amountAfterFee == amount` and the depositor receives wrsETH/rsETH priced on the full deposited amount with no fee deducted.

The threshold below which the fee rounds to zero is `amount < 10_000 / feeBps`:

| `feeBps` | Fee-free threshold |
|---|---|
| 5 (0.05%) | < 2 000 wei |
| 10 (0.1%) | < 1 000 wei |
| 50 (0.5%) | < 200 wei |

The only guard against small deposits is `if (amount == 0) revert InvalidAmount()` — there is no `minAmountToDeposit` check in any pool contract (contrast with `LRTDepositPool._beforeDeposit`, which does enforce `minAmountToDeposit`).

An attacker on any supported L2 (Arbitrum, Optimism, Base, Linea, Unichain, etc.) can:
1. Call `deposit()` with `amount = threshold - 1` in a tight loop.
2. Each call mints wrsETH/rsETH at the full-amount rate with `fee = 0`.
3. After N iterations the attacker holds `N * (threshold - 1)` wei worth of wrsETH/rsETH while `feeEarnedInETH` (or `feeEarnedInToken`) remains at zero.
4. The fee recipient (`BRIDGER_ROLE`) calls `withdrawFees()` and receives nothing.

The attack is directly analogous to the referenced M-3 finding: dust-amount repetition exploits rounding to zero to bypass a fee mechanism, causing the protocol to lose yield it was entitled to collect.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Protocol fees are the unclaimed yield of the fee recipient. By making `fee = 0` on every deposit, the attacker causes `feeEarnedInETH` / `feeEarnedInToken` to never accumulate, so `withdrawFees()` transfers nothing. The attacker receives wrsETH/rsETH at a better-than-intended rate (full amount, no fee), and the fee recipient is permanently deprived of the revenue. The loss scales linearly with the number of dust deposits and is bounded only by the attacker's patience and gas budget.

---

### Likelihood Explanation

**High.** All affected pool contracts are deployed on multiple L2 networks where gas costs are negligible (sub-cent per transaction post-EIP-4844). The entry point (`deposit()`) is fully public with no access control. No minimum deposit amount exists in any pool contract. The attack requires no special setup, no collateral, and no coordination — only repeated calls with a small ETH or token amount.

---

### Recommendation

Enforce a minimum fee or a minimum deposit amount in every pool's `deposit()` function, analogous to the fix proposed in the referenced report:

```diff
// In viewSwapRsETHAmountAndFee (ETH variant)
fee = amount * feeBps / 10_000;
+if (feeBps > 0 && fee == 0) revert InvalidAmount(); // fee must be non-zero when feeBps is set
```

Alternatively, enforce a minimum deposit amount in each pool contract (mirroring `LRTDepositPool.minAmountToDeposit`):

```diff
+uint256 public minDepositAmount;

function deposit(string memory referralId) external payable ... {
    uint256 amount = msg.value;
+   if (amount < minDepositAmount) revert InvalidAmount();
    ...
}
```

---

### Proof of Concept

Assume `feeBps = 5` (0.05%) and `rsETHToETHrate = 1.05e18` (rsETH trades at a 5% premium to ETH).

- Fee-free threshold: `10_000 / 5 = 2_000` wei.
- Each call: `deposit{value: 1_999}("")` → `fee = 1_999 * 5 / 10_000 = 0`.
- wrsETH minted per call: `1_999 * 1e18 / 1.05e18 ≈ 1_904` wei of wrsETH.
- After 10 000 calls (trivial on any L2): attacker holds ~19 040 000 wei of wrsETH backed by ~19 990 000 wei of ETH, having paid **zero fees**.
- `feeEarnedInETH` remains `0`; the fee recipient receives nothing on `withdrawFees()`.

The exact same loop works for ERC-20 token deposits via `deposit(token, amount, referralId)` in all pool variants.

**Affected lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

**No minimum deposit guard (contrast with L1):** [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/pools/RSETHPool.sol (L312-312)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPool.sol (L336-336)
```text
        fee = amount * feeBpsForToken / 10_000;
```

**File:** contracts/pools/RSETHPoolV3.sol (L282-282)
```text
        if (amount == 0) revert InvalidAmount();
```

**File:** contracts/pools/RSETHPoolV3.sol (L300-300)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPoolV3.sol (L324-324)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L278-278)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L301-301)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L419-419)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L442-442)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```
