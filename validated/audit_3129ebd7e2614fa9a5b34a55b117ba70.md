Audit Report

## Title
Zero rsETH Minted on Dust Deposits Due to Truncating Division in `getRsETHAmountToMint` - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getRsETHAmountToMint()` uses Solidity integer division that truncates toward zero, producing `rsethAmountToMint = 0` for sufficiently small deposits. When a caller passes `minRSETHAmountExpected = 0`, the zero-mint guard does not revert, and the user's deposited assets are transferred into the pool while zero rsETH is issued in return.

## Finding Description
`getRsETHAmountToMint` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

For ETH-pegged LSTs (`assetPrice ≈ 1e18`) and any `rsETHPrice > 1e18` (normal post-reward state), a deposit of 1 wei yields `(1 * 1e18) / 1.05e18 = 0` due to integer truncation.

The only guard in `_beforeDeposit` is:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
``` [2](#0-1) 

When `minRSETHAmountExpected = 0`, the condition `0 < 0` is false and execution continues. The `depositAmount == 0` guard does not help because a 1-wei deposit is nonzero, and `minAmountToDeposit` defaults to `0`: [3](#0-2) 

Execution then reaches `depositAsset`:

```solidity
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
_mintRsETH(rsethAmountToMint);   // rsethAmountToMint == 0
``` [4](#0-3) 

`_mintRsETH` calls `RSETH.mint(msg.sender, 0)`. The `checkDailyMintLimit` modifier evaluates `currentPeriodMintedAmount + 0 > maxMintAmountPerDay`, which is false for any positive `maxMintAmountPerDay`, so it does not revert. OpenZeppelin's `_mint` does not revert on a zero amount. The transaction succeeds silently. [5](#0-4) [6](#0-5) 

## Impact Explanation
The depositor's assets are permanently absorbed into the pool TVL (increasing the value of all existing rsETH holders pro-rata) while the depositor receives zero rsETH. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value at the protocol level.**

## Likelihood Explanation
- `rsETHPrice > 1e18` is the normal operating state once any staking rewards accrue.
- `minAmountToDeposit` defaults to `0`; many deployments may leave it unset.
- Any caller who passes `minRSETHAmountExpected = 0` (a valid argument) and deposits a dust amount triggers the bug without any special privileges.
- Integrating contracts (routers, aggregators) that do not enforce a minimum received amount are particularly at risk.

**Likelihood**: Low — requires a dust-sized deposit with no slippage protection, but conditions are realistic for naive integrators.

## Recommendation
1. Add an explicit zero-mint guard in `_beforeDeposit`:
   ```solidity
   if (rsethAmountToMint == 0) revert ZeroRsETHMinted();
   ```
2. Set a non-zero `minAmountToDeposit` at initialization to ensure deposits always produce at least 1 wei of rsETH.
3. Document that callers must pass a non-zero `minRSETHAmountExpected` to protect against rounding losses, analogous to slippage protection in AMMs.

## Proof of Concept
Assume: `rsETHPrice = 1.05e18`, `assetPrice = 1e18`, `minAmountToDeposit = 0` (default).

```
depositPool.depositAsset(stETH, 1, 0, "");
```

1. `_beforeDeposit(stETH, 1, 0)` — `depositAmount = 1 > 0`, passes.
2. `getRsETHAmountToMint(stETH, 1)` → `(1 * 1e18) / 1.05e18 = 0`.
3. Guard: `0 < 0` → false → no revert.
4. `safeTransferFrom(msg.sender, depositPool, 1)` — 1 wei of stETH leaves the user.
5. `RSETH.mint(msg.sender, 0)` — zero rsETH minted, no revert.
6. Transaction succeeds; user's 1 wei is permanently absorbed into pool TVL.

A Foundry fuzz test parameterizing `amount` over `[1, rsETHPrice/assetPrice - 1]` and asserting `rsethAmountToMint > 0` would reproduce this for all dust values in that range.

### Citations

**File:** contracts/LRTDepositPool.sol (L114-115)
```text
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/RSETH.sol (L42-55)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
```

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }
```
