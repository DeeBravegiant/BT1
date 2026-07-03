Audit Report

## Title
Zero wrsETH Minted on Dust Deposits Due to Missing Zero-Amount Output Guard - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `deposit` functions across `RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge` compute the wrsETH/rsETH output via integer division that truncates to zero for small inputs, yet no guard reverts when the result is zero. A user who sends a non-zero ETH or token amount can receive zero wrsETH in return, with their deposited ETH permanently absorbed into the pool.

## Finding Description
In `RSETHPoolV3.viewSwapRsETHAmountAndFee` (line 307), the ETH-path share calculation is:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

`rsETHToETHrate` is the rsETH/ETH exchange rate at 1e18 precision, starting at 1e18 and growing monotonically as yield accrues. Once `rsETHToETHrate > 1e18` (normal operating state), any deposit where `amountAfterFee * 1e18 < rsETHToETHrate` produces `rsETHAmount = 0` via Solidity truncation.

The `deposit(string)` function at lines 246–265 only guards against zero input:

```solidity
if (amount == 0) revert InvalidAmount();
```

It does not guard against zero output. After `viewSwapRsETHAmountAndFee` returns `rsETHAmount = 0`, execution continues unconditionally to `wrsETH.mint(msg.sender, 0)` (line 262), minting nothing while the deposited ETH is retained by the contract. The same pattern is confirmed in `RSETHPoolNoWrapper` (lines 235, 241), `RSETHPoolV3ExternalBridge` (lines 375, 381), and `RSETHPoolV3WithNativeChainBridge` (lines 292, 298). A grep across all pool contracts confirms zero `rsETHAmount > 0` or `rsETHAmount != 0` guards exist anywhere.

## Impact Explanation
**Low — Contract fails to deliver promised returns.** A depositor sending dust ETH (e.g., 1 wei) when `rsETHToETHrate > 1e18` has their ETH accepted and permanently held in the pool, while receiving 0 wrsETH. The transaction succeeds silently with no revert. The deposited ETH accrues to existing wrsETH holders. Per-transaction loss is bounded to dust amounts, but the contract unambiguously fails to deliver its promised return without reverting.

## Likelihood Explanation
The condition is reachable by any unprivileged external caller via the public `deposit` functions. No special role, front-running, or external dependency is required. The condition activates as soon as `rsETHToETHrate` exceeds 1e18, which is the normal post-launch state of the protocol. Any scripting error, test transaction, or wallet rounding that sends 1 wei will silently lose it.

## Recommendation
Add a zero-output guard immediately after `viewSwapRsETHAmountAndFee` in every deposit function across all four pool variants:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

Apply the same guard to the token deposit path (`deposit(address,uint256,string)`) in each pool variant.

## Proof of Concept
Assume `rsETHToETHrate = 1.05e18` (5% yield accrued).

1. Alice calls `RSETHPoolV3.deposit{value: 1}("")` (1 wei ETH).
2. `fee = 1 * feeBps / 10_000 = 0` (rounds down for any reasonable `feeBps`).
3. `amountAfterFee = 1`.
4. `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (Solidity truncation).
5. `feeEarnedInETH += 0`.
6. `wrsETH.mint(Alice, 0)` — Alice receives 0 wrsETH.
7. Alice's 1 wei is permanently held in the pool.

The transaction succeeds with no revert. Alice has no way to detect the zero-output outcome before submitting because there is no `minRSETHAmountExpected` parameter and `viewSwapRsETHAmountAndFee` is a view function she could call to pre-check, but the contract itself does not enforce a non-zero output.

**Foundry fuzz test plan:** Fuzz `deposit{value: amount}` with `amount` in range `[1, rsETHToETHrate / 1e18]` against a fork where `getRate()` returns `> 1e18`. Assert that `wrsETH.balanceOf(depositor) > 0` after each call — this assertion will fail for all dust inputs, confirming the bug. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L256-262)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L235-241)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L375-381)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L292-298)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```
