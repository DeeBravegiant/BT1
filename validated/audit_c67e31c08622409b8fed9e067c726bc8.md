Audit Report

## Title
Missing Minimum Output Guard in L2 Pool Deposit Functions Allows Silent Shortfall Against Previewed Amount - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

## Summary
The `deposit` functions in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` compute the minted `wrsETH` amount entirely from the live oracle rate at execution time, with no caller-supplied floor. Because the rsETH oracle rate increases monotonically as staking rewards accumulate, any oracle update between a user's preview call and their deposit execution silently reduces the minted output below the previewed amount, with no on-chain revert path. The L1 `LRTDepositPool` already enforces a `minRSETHAmountExpected` guard; the L2 pools are missing the equivalent.

## Finding Description
In `RSETHPoolV3.deposit(string)` (L246–265) and `deposit(address,uint256,string)` (L271–293), the minted amount is computed by `viewSwapRsETHAmountAndFee`, which calls `getRate()` at execution time to obtain the live `rsETHToETHrate` and divides: `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`. No parameter allows the caller to bound this output. `RSETHPoolV3ExternalBridge.deposit(string)` (L366–384) and its token variant (L390–412) are structurally identical. By contrast, `LRTDepositPool.depositETH` (L76–93) accepts `minRSETHAmountExpected` and enforces it inside `_beforeDeposit` (L665–669): `if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet()`. The L2 pools have no equivalent check. Because the oracle rate only moves upward under normal operation, every deposit is structurally exposed: a user who previews via `viewSwapRsETHAmountAndFee` off-chain and then submits a deposit will receive fewer `wrsETH` than observed if any oracle update is processed before their transaction is mined, and the transaction will not revert.

## Impact Explanation
The depositor's ETH or LST is fully consumed. The `wrsETH` minted is determined by the oracle rate at execution time, which the user cannot bound. The user's principal is not destroyed — it is converted at the updated rate — but the contract fails to deliver the token quantity the user observed and intended to accept. This matches the allowed Low impact: *contract fails to deliver promised returns, but doesn't lose value*. The L1 contract's explicit `minRSETHAmountExpected` guard confirms the protocol recognises this as a required protection; its absence from the L2 pools is a concrete functional gap, not merely a stylistic omission.

## Likelihood Explanation
No special role or privilege is required. Any ordinary depositor calling `deposit` on either L2 pool is affected. The rsETH oracle rate increases monotonically as staking rewards accumulate, so the condition is persistent and structural: it applies to every deposit on both contracts whenever any oracle update occurs between preview and execution. No attacker action is needed; normal protocol operation produces the shortfall.

## Recommendation
Add a `minWrsETHAmount` parameter to all `deposit` overloads in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge`. After computing `rsETHAmount` via `viewSwapRsETHAmountAndFee`, add: `if (rsETHAmount < minWrsETHAmount) revert MinimumAmountToReceiveNotMet();` — consistent with the pattern already enforced in `LRTDepositPool._beforeDeposit` (L667–669).

## Proof of Concept
1. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain; observes expected output `X` wrsETH at current oracle rate `R`.
2. User submits `deposit{value: 1 ether}("ref")` to `RSETHPoolV3`.
3. Before the transaction is mined, the rsETH oracle rate is updated to `R' > R` (normal staking-reward accrual).
4. Inside the transaction, `viewSwapRsETHAmountAndFee` re-evaluates: `rsETHAmount = amountAfterFee * 1e18 / R'` yields `X' < X`.
5. `wrsETH.mint(msg.sender, X')` executes (L262); no revert occurs; user receives `X' < X` with no on-chain recourse.
6. Repeat for `RSETHPoolV3ExternalBridge.deposit` (L381) and both token-deposit variants — all four paths are identically unguarded.

Foundry fork test plan: fork the deployed L2, record the oracle rate, advance time or simulate an oracle update to increment the rate, then call `deposit` and assert `wrsETH.balanceOf(user) < previewedAmount` with no revert — demonstrating the shortfall is silent and unavoidable.