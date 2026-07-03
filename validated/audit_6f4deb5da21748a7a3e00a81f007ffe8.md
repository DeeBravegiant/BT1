Audit Report

## Title
Zero wrsETH Minted on Dust Deposits Due to Integer Division Truncation — (File: contracts/pools/RSETHPoolV3.sol)

## Summary
Every L2 pool deposit function computes `rsETHAmount` via integer division in `viewSwapRsETHAmountAndFee`. When the numerator is smaller than the denominator, Solidity truncates the result to zero. No post-computation guard exists on the output, so a deposit of 1 wei is accepted, the ETH is retained by the pool, and `wrsETH.mint(msg.sender, 0)` executes without revert — permanently depriving the caller of any claim on their deposited assets.

## Finding Description
In `RSETHPoolV3.sol`, the ETH deposit path is:

```solidity
// L256
if (amount == 0) revert InvalidAmount();
// L258
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
// L260
feeEarnedInETH += fee;
// L262
wrsETH.mint(msg.sender, rsETHAmount);   // executes with rsETHAmount == 0
```

`viewSwapRsETHAmountAndFee` computes:

```solidity
// L307
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

With `rsETHToETHrate ≈ 1.05e18`, any `amountAfterFee ≤ 1` produces `rsETHAmount = 0`. The only guard (`amount == 0`) checks the raw input, not the computed output. The identical pattern is present in:

- `RSETHPoolV2.sol` L233 / deposit L216
- `RSETHPool.sol` L319, L346 / deposit L275, L302
- `RSETHPoolNoWrapper.sol` L285, L311
- `RSETHPoolV3ExternalBridge.sol` L426, L452
- `RSETHPoolV3WithNativeChainBridge.sol` L343, L370

For the token path the division is `amountAfterFee * tokenToETHRate / rsETHToETHrate`; when `tokenToETHRate < rsETHToETHrate` the zero-output threshold rises to a few wei, but remains reachable.

## Impact Explanation
A depositor who triggers the rounding condition sends ETH or ERC-20 tokens to the pool and receives zero wrsETH. The deposited assets are pooled with the collective balance and eventually bridged to L1; the depositor holds no receipt token and has no mechanism to reclaim their contribution. This constitutes **permanent freezing of funds** for the affected depositor. The per-transaction loss is at most a few wei, placing practical severity at Low, and matching **Low — Contract fails to deliver promised returns** from the allowed impact scope.

## Likelihood Explanation
Low. For the ETH path the condition requires `amountAfterFee = 1` (i.e., a 1-wei deposit with zero fee, or a deposit small enough that `fee` rounds to zero and `amountAfterFee` is still 1). For the token path the threshold is slightly higher when `tokenToETHRate < rsETHToETHrate` but still only a few wei. Accidental triggering by a normal user is extremely unlikely; deliberate triggering by a contract sending repeated 1-wei deposits is possible but the per-call loss is negligible.

## Recommendation
Add a zero-output guard immediately after computing `rsETHAmount` in every deposit function across all pool variants:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
require(rsETHAmount > 0, "zero rsETH output");
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

Apply the same guard to the token-deposit overload and to every pool variant listed above.

## Proof of Concept
1. Deploy `RSETHPoolV3` on a local fork; configure `rsETHToETHrate = 1.05e18` (realistic current rate).
2. Call `deposit("")` with `msg.value = 1 wei`.
3. Inside `viewSwapRsETHAmountAndFee`: `fee = 1 * feeBps / 10_000 = 0`; `amountAfterFee = 1`; `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`.
4. `wrsETH.mint(msg.sender, 0)` executes without revert.
5. Assert: caller's wrsETH balance is 0; pool's ETH balance increased by 1 wei.
6. Repeat for the token path with a token whose `tokenToETHRate < rsETHToETHrate` and `amount = 1`.

A Foundry fuzz test parameterising `amount` over `[1, rsETHToETHrate / 1e18]` will reliably reproduce the zero-output condition across all pool variants.