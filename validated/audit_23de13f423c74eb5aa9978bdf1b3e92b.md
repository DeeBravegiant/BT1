Audit Report

## Title
Zero wrsETH Minted for Dust ETH/Token Deposits Due to Integer Division Truncation in `viewSwapRsETHAmountAndFee` - (File: contracts/pools/RSETHPoolV2.sol, RSETHPoolV3.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

## Summary
Every L2 RSETHPool variant computes the wrsETH output via integer division `amountAfterFee * 1e18 / rsETHToETHrate`. When the deposit amount is small enough that `amountAfterFee * 1e18 < rsETHToETHrate`, the result truncates to zero. The `deposit` function checks only that `amount != 0` but never verifies the computed `rsETHAmount` before calling `wrsETH.mint(msg.sender, rsETHAmount)`, so the ETH is accepted and held by the pool while the depositor receives zero wrsETH tokens.

## Finding Description
In every affected pool contract, `viewSwapRsETHAmountAndFee` computes:

```solidity
// RSETHPoolV2.sol L233
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

`rsETHToETHrate` is always ≥ 1e18 at launch and grows monotonically as staking rewards accrue. Solidity integer division truncates toward zero, so any deposit where `amountAfterFee * 1e18 < rsETHToETHrate` yields `rsETHAmount = 0`.

The `deposit` function in each pool variant guards only against a zero `amount`:

```solidity
// RSETHPoolV2.sol L210-216
if (amount == 0) revert InvalidAmount();
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // rsETHAmount can be 0
```

OpenZeppelin's `_mint` does not revert on a zero amount — it silently emits a `Transfer(address(0), to, 0)` event and returns. The depositor's ETH is now held by the pool with no corresponding wrsETH issued and no mechanism to recover it.

The same truncation applies to token deposits in `RSETHPoolV3` and its variants via `amountAfterFee * tokenToETHRate / rsETHToETHrate`.

Existing guard reviewed and found insufficient: the `amount != 0` check at L210 only prevents a zero-value call; it does not prevent the computed output from being zero after division.

## Impact Explanation
A depositor who sends a dust ETH or token amount receives zero wrsETH while their funds remain in the pool contract, permanently unrecoverable by the depositor. The contract accepted the deposit and failed to deliver the promised token return. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value** from the allowed impact scope.

## Likelihood Explanation
Since `rsETHToETHrate` starts at 1e18 and grows, the truncation threshold is always at least 1 wei and increases over time. Any caller who sends 1 wei ETH (e.g., via a script, wallet dust, or direct contract call) will trigger this silently. No special privileges are required; the `deposit` function is publicly callable with no minimum deposit enforcement. The scenario is repeatable on every pool variant across all deployed L2 chains.

## Recommendation
Add a zero-check on the computed `rsETHAmount` immediately after calling `viewSwapRsETHAmountAndFee`, and revert if it is zero:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

Apply the same guard to the token-deposit overload. This mirrors the protection in `LRTDepositPool._beforeDeposit`, which reverts when `rsethAmountToMint < minRSETHAmountExpected`.

## Proof of Concept
Assume `rsETHToETHrate = 1.05e18` (rsETH has appreciated 5% since launch).

1. Any external caller invokes `RSETHPoolV2.deposit{value: 1}("")`.
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * feeBps / 10_000 = 0` (rounds down for any `feeBps < 10_000`)
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (truncated)
3. `feeEarnedInETH += 0`.
4. `wrsETH.mint(msg.sender, 0)` — OpenZeppelin `_mint` succeeds silently, minting nothing.
5. The 1 wei ETH is held by the pool; the caller holds zero wrsETH.

**Foundry fuzz test plan:**
```solidity
function testFuzz_zeroMintOnDustDeposit(uint256 amount) public {
    vm.assume(amount > 0 && amount < rsETHToETHrate / 1e18 + 1);
    uint256 balBefore = wrsETH.balanceOf(user);
    vm.deal(user, amount);
    vm.prank(user);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(user), balBefore); // user received 0 wrsETH
    assertGt(address(pool).balance, 0);           // ETH is held by pool
}
```

The same scenario applies to token deposits via `deposit(address token, uint256 amount, ...)` when `amountAfterFee * tokenToETHRate < rsETHToETHrate`.