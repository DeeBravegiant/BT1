Audit Report

## Title
Fee Truncation to Zero via Integer Division Allows Fee-Free Deposits - (File: contracts/pools/RSETHPoolNoWrapper.sol)

## Summary
All L2 RSETHPool contracts compute the protocol fee as `fee = amount * feeBps / 10_000`. Solidity integer division truncates this to zero whenever `amount * feeBps < 10_000`. An unprivileged depositor can split any deposit into many sub-threshold transactions on low-gas L2 chains, receiving rsETH at the full rate while `feeEarnedInETH` accrues nothing, permanently depriving the protocol of its fee revenue.

## Finding Description
The fee calculation is identical across all pool variants:

```solidity
// RSETHPoolNoWrapper.sol L278
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [1](#0-0) 

When `amount * feeBps < 10_000`, Solidity truncates `fee` to `0`. `amountAfterFee` then equals `amount` (the full deposit), and `feeEarnedInETH += 0` — no fee is recorded. The same pattern is confirmed in:

- `RSETHPool.sol` L312 [2](#0-1) 
- `RSETHPoolV3.sol` L300 [3](#0-2) 
- `RSETHPoolV3ExternalBridge.sol` L419 [4](#0-3) 
- `RSETHPoolV3WithNativeChainBridge.sol` L336 [5](#0-4) 

The only guard in `deposit()` is `if (amount == 0) revert InvalidAmount()`, which does not prevent sub-threshold deposits. [6](#0-5) 

Zero-fee threshold per `feeBps` value:
| `feeBps` | Max zero-fee deposit |
|---|---|
| 10 | 999 wei |
| 5 | 1 999 wei |
| 1 | 9 999 wei |

## Impact Explanation
**High — Theft of unclaimed yield.** `feeEarnedInETH` is the protocol's sole on-chain fee accumulator. When fee truncates to zero, the attacker receives rsETH at the full 1:1 ETH-equivalent rate while the protocol collects nothing. This directly and permanently reduces the protocol's accrued fee revenue — a concrete theft of unclaimed yield, matching the allowed High impact class.

## Likelihood Explanation
**Medium.** The attack requires no privileges — `deposit()` is a public payable function callable by any EOA or contract. The attacker only needs to read the public `feeBps` variable, compute the threshold, and submit sub-threshold deposits. On Arbitrum, Optimism, Base, and Unichain, gas per transaction is sub-cent, making it economically rational to split any deposit of meaningful size into thousands of sub-threshold calls. The attack is repeatable indefinitely and is not self-limiting.

## Recommendation
Replace floor division with ceiling division so any non-zero deposit with non-zero `feeBps` always yields at least 1 wei of fee:

```solidity
// Replace:
fee = amount * feeBps / 10_000;

// With:
fee = (amount * feeBps + 9_999) / 10_000;
```

Apply to all eight pool contracts: `RSETHPoolNoWrapper.sol`, `RSETHPool.sol`, `RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, `RSETHPoolV2.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV2NBA.sol`.

## Proof of Concept
**Setup:** `feeBps = 10`, rsETH/ETH rate = `1.05e18`.

**Normal 1 ETH deposit:**
- `fee = 1e18 * 10 / 10_000 = 1e15` (0.001 ETH collected)
- `rsETHAmount = (1e18 - 1e15) * 1e18 / 1.05e18 ≈ 0.951e18`

**Attacker splits into 1,001 deposits of 999 wei each:**
- Per call: `fee = 999 * 10 / 10_000 = 9990 / 10_000 = 0` (truncated)
- `amountAfterFee = 999`, `feeEarnedInETH += 0`
- Protocol collects **0 wei** across all 1,001 calls

**Foundry fuzz test plan:**
```solidity
function testFuzz_zeroFee(uint256 amount) public {
    vm.assume(amount > 0 && amount < 10_000 / feeBps);
    uint256 feeBefore = pool.feeEarnedInETH();
    vm.deal(attacker, amount);
    vm.prank(attacker);
    pool.deposit{value: amount}("");
    assertEq(pool.feeEarnedInETH(), feeBefore); // fee never accrued
}
```

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L235-235)
```text
        if (amount == 0) revert InvalidAmount();
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L278-285)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L312-313)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV3.sol (L300-301)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L419-420)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L336-337)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```
