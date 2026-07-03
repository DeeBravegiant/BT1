Audit Report

## Title
Integer Division Truncation in `viewSwapRsETHAmountAndFee` Silently Accepts Deposits While Minting Zero rsETH - (File: `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

## Summary
All four L2 deposit pool variants compute rsETH output via integer division that truncates to zero for dust-level deposits. Because no post-computation guard checks whether `rsETHAmount == 0`, a depositor's ETH (or tokens) are accepted by the pool while zero rsETH/wrsETH is transferred back. The depositor permanently loses their dust deposit with no recovery path.

## Finding Description
Every pool variant computes the rsETH output in `viewSwapRsETHAmountAndFee` as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Because rsETH is a yield-bearing token, `rsETHToETHrate` is always strictly greater than `1e18` in normal operation. For any `amountAfterFee` where `amountAfterFee * 1e18 < rsETHToETHrate`, Solidity integer division truncates to zero.

The ETH deposit functions in all four contracts guard only against a zero input:

```solidity
if (amount == 0) revert InvalidAmount();
```

There is no subsequent check on the computed `rsETHAmount`. Execution proceeds to transfer/mint zero tokens:

- `RSETHPoolNoWrapper.sol`: `rsETH.safeTransfer(msg.sender, 0)` — user's ETH is retained by the pool.
- `RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`: `wrsETH.mint(msg.sender, 0)` — user's ETH is retained by the pool.

The retained ETH is swept into the pool's aggregate balance and bridged to L1 in the next `bridgeAssets` call, with no per-user accounting or refund mechanism. By contrast, the L1 `LRTDepositPool._beforeDeposit` enforces both a `minAmountToDeposit` floor and a caller-supplied `minRSETHAmountExpected` slippage guard — neither protection exists in any L2 pool.

## Impact Explanation
A depositor who sends a dust amount (e.g., 1 wei of ETH) receives zero rsETH/wrsETH while their ETH is permanently absorbed into the pool and eventually bridged to L1 as undifferentiated pool ETH. The protocol does not lose value, but the depositor loses their entire deposit with no recourse. This matches the **Low** allowed impact: *Contract fails to deliver promised returns, but doesn't lose value.*

## Likelihood Explanation
Any unprivileged external caller can trigger this deterministically by calling `deposit{value: 1}("")`. No special role, governance action, or front-running is required. The condition holds whenever `rsETHToETHrate > 1e18`, which is always true in normal operation. Accidental triggering is rare (dust amounts), but deliberate triggering is trivially easy and repeatable.

## Recommendation
Add a zero-output guard in every `deposit` function (or inside `viewSwapRsETHAmountAndFee`) across all pool variants:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

Alternatively, enforce a minimum deposit floor analogous to `LRTDepositPool`'s `minAmountToDeposit`, or require callers to supply a `minRsETHAmountExpected` slippage parameter checked before accepting funds.

## Proof of Concept
Applies to `RSETHPoolNoWrapper` (and identically to all other pool variants):

1. Deploy or use an existing `RSETHPoolNoWrapper` instance where `rsETHToETHrate > 1e18` (always true in normal operation).
2. Call `deposit{value: 1}("")` — sending exactly 1 wei of ETH.
3. Inside `viewSwapRsETHAmountAndFee`:
   - `fee = 1 * feeBps / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (truncated)
4. `rsETH.safeTransfer(msg.sender, 0)` executes without revert.
5. Caller holds 0 additional rsETH; pool retains 1 wei.
6. The 1 wei is included in the next `bridgeAssets` call — permanently unrecoverable by the depositor.

**Foundry fuzz test sketch:**
```solidity
function testFuzz_dustDepositMintsZero(uint256 amount) public {
    vm.assume(amount > 0 && amount * 1e18 < rsETHToETHrate);
    uint256 balBefore = rsETH.balanceOf(address(this));
    pool.deposit{value: amount}("");
    assertEq(rsETH.balanceOf(address(this)), balBefore); // received nothing
}
```