Audit Report

## Title
Zero rsETH Minting on Sub-Threshold Deposits Silently Burns User Funds - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

## Summary
All three L2 pool contracts compute `rsETHAmount` via integer division without a subsequent zero-output guard. When a depositor sends an ETH or token amount smaller than the rounding threshold, the contract accepts the funds, mints or transfers zero wrsETH/rsETH, and emits a success event — permanently retaining the depositor's value with no revert.

## Finding Description
In all three pool contracts, the ETH deposit path computes:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

and the token deposit path computes:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

When `amountAfterFee * 1e18 < rsETHToETHrate` (currently ≈ 1.05 × 10¹⁸), Solidity truncates to zero. The deposit functions then proceed unconditionally:

- `RSETHPoolV3.sol` line 262: `wrsETH.mint(msg.sender, rsETHAmount)` — mints 0, no revert.
- `RSETHPoolNoWrapper.sol` line 241: `rsETH.safeTransfer(msg.sender, rsETHAmount)` — transfers 0, no revert.
- `RSETHPoolV3ExternalBridge.sol` line 381: `wrsETH.mint(msg.sender, rsETHAmount)` — mints 0, no revert.

The only existing guard is `if (amount == 0) revert InvalidAmount()`, which does not protect against a nonzero `amount` that produces a zero output. For token deposits, `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)` executes before the output is computed, so the user's tokens are already transferred in before the zero result is discovered. By contrast, `LRTDepositPool._beforeDeposit` enforces both a `minAmountToDeposit` floor and a `minRSETHAmountExpected` slippage check; none of the L2 pool contracts have either.

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

A depositor who sends any ETH or token amount below the rounding threshold (currently ~1 wei for ETH, growing as the rsETH exchange rate appreciates) receives zero wrsETH/rsETH while their funds are retained by the pool. The deposited value is not returned and not credited. The emitted `SwapOccurred` event carries `rsETHAmount = 0`, giving no on-chain indication of failure. The per-transaction loss is minimal today but the threshold grows monotonically with the rsETH rate, and integrators or smart-contract callers that do not inspect return values will silently lose funds.

## Likelihood Explanation
Any unprivileged depositor can trigger this by sending a sufficiently small ETH value (e.g., 1 wei) to any of the three public payable `deposit(string)` entry points, or a sufficiently small token amount to any `deposit(address,uint256,string)` entry point. No front-running, oracle manipulation, or privileged action is required. The condition is deterministic and reproducible at any time.

## Recommendation
Add a zero-output guard in each `deposit()` overload immediately after computing `rsETHAmount`:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert ZeroRsETHMinted();
```

Apply this to all ETH and token deposit overloads in `RSETHPoolV3`, `RSETHPoolNoWrapper`, and `RSETHPoolV3ExternalBridge`. Optionally, add a `minRSETHAmountExpected` parameter (mirroring `LRTDepositPool._beforeDeposit`) to give callers explicit slippage control.

## Proof of Concept
Assume `rsETHToETHrate = 1.05e18`:

1. Alice calls `RSETHPoolV3.deposit{value: 1}("")` (1 wei ETH).
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * feeBps / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer truncation)
3. `feeEarnedInETH += 0`
4. `wrsETH.mint(Alice, 0)` — succeeds silently, Alice receives 0 wrsETH.
5. Alice's 1 wei ETH is held by the pool with no corresponding wrsETH issued.
6. `SwapOccurred(Alice, 0, 0, "")` is emitted — no revert, no indication of failure.

Foundry fuzz test plan:
```solidity
function testFuzz_zeroMintOnSmallDeposit(uint256 amount) public {
    uint256 rate = pool.getRate(); // e.g. 1.05e18
    vm.assume(amount > 0 && amount * 1e18 < rate);
    vm.deal(alice, amount);
    vm.prank(alice);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(alice), 0);
    assertEq(address(pool).balance, amount); // funds retained
}
```