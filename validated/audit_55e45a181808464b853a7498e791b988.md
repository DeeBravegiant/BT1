Audit Report

## Title
Missing Zero-Value Check on Computed rsETH Mint Amount Allows Silent Loss of Deposited Assets - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool._beforeDeposit` computes `rsethAmountToMint` via integer division and validates it only against the caller-supplied `minRSETHAmountExpected`. When `minRSETHAmountExpected = 0` and the computed mint amount truncates to zero, the slippage guard `0 < 0` evaluates to false and does not revert. The depositor's ETH or LST is consumed by the protocol while `_mintRsETH(0)` issues nothing in return. The same structural gap exists across all L2 RSETHPool variants, which have no slippage parameter at all.

## Finding Description
`getRsETHAmountToMint` at `contracts/LRTDepositPool.sol:520` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Solidity integer division truncates toward zero. When `amount * getAssetPrice(asset) < rsETHPrice()`, the result is 0.

`_beforeDeposit` at lines 648–670 then checks:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

There is no `require(rsethAmountToMint > 0)` guard. `minAmountToDeposit` is not set in `initialize` and defaults to 0 (line 30), so 1-wei deposits pass the `depositAmount == 0 || depositAmount < minAmountToDeposit` check at line 657. When `minRSETHAmountExpected = 0`, the condition `0 < 0` is false and execution continues to `_mintRsETH(0)` (line 90 for ETH, line 115 for LST), minting nothing.

For `depositETH`, the ETH is already held by the contract as `msg.value` before `_mintRsETH` is called. For `depositAsset`, the token transfer at line 114 occurs after `_beforeDeposit` passes, then `_mintRsETH(0)` is called.

In all L2 RSETHPool variants (`RSETHPool.sol:319`, `RSETHPoolV2.sol:233`, `RSETHPoolV3.sol:262`), `viewSwapRsETHAmountAndFee` computes `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` with the same truncation risk, and the `deposit` functions unconditionally mint or transfer `rsETHAmount` with no zero guard and no slippage parameter.

## Impact Explanation
**Low. Contract fails to deliver promised returns, but doesn't lose value.**

A depositor whose computed mint amount truncates to zero has their deposited asset retained by the protocol while receiving 0 rsETH/wrsETH. The protocol's TVL increases; the user receives nothing. This is a concrete, non-hypothetical failure of the contract's core promise.

## Likelihood Explanation
Any unprivileged external caller can trigger this by:
- Calling `depositETH{value: 1}(0, "")` on mainnet where `rsETHPrice > 1e18` (staking rewards have accrued), producing `(1 * 1e18) / rsETHPrice = 0`.
- Calling any L2 RSETHPool `deposit` with a dust amount where `amountAfterFee * 1e18 < rsETHToETHrate`.

No special privileges, oracle manipulation, or external compromise is required. The condition is reachable with a single public call. On L2 RSETHPool contracts the risk is structural — there is no slippage parameter to set.

## Recommendation
Add an explicit non-zero check in `_beforeDeposit` immediately after computing `rsethAmountToMint`:

```solidity
if (rsethAmountToMint == 0) revert ZeroRsETHMintAmount();
```

Apply the same guard in every RSETHPool `deposit` function after computing `rsETHAmount`. Additionally, consider enforcing `minAmountToDeposit > 0` in `initialize` to prevent dust deposits that are economically guaranteed to truncate.

## Proof of Concept
1. `rsETHPrice` on mainnet is ~1.05e18 (staking rewards accrued).
2. User calls `LRTDepositPool.depositETH{value: 1}(0, "")`.
3. `_beforeDeposit`: `depositAmount = 1`, passes `depositAmount == 0` check (line 657) since `minAmountToDeposit = 0`.
4. `getRsETHAmountToMint`: `(1 * 1e18) / 1.05e18 = 0` (integer truncation, line 520).
5. Slippage check: `0 < 0 = false` → no revert (line 667).
6. `_mintRsETH(0)` called (line 90) → 0 rsETH minted; user's 1 wei ETH is held by the pool.
7. For RSETHPoolV2: user calls `deposit{value: 1}("")`; `viewSwapRsETHAmountAndFee(1)` returns `rsETHAmount = 0`; `wrsETH.mint(msg.sender, 0)` is called (line 216); user's 1 wei ETH is in the contract, user receives 0 wrsETH.