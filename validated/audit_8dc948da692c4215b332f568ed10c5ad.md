Audit Report

## Title
Stale `rsETHPrice` Used in Deposit and Withdrawal Calculations Allows Theft of Unclaimed Yield - (`contracts/LRTDepositPool.sol`)

## Summary

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called by an external keeper. Neither `depositETH()`, `depositAsset()`, nor `initiateWithdrawal()` refreshes this price before computing mint or redemption amounts. When staking rewards accrue and the stored price lags behind the true price, a depositor receives more rsETH than deserved, diluting existing holders and stealing their unclaimed yield.

## Finding Description

`LRTOracle.rsETHPrice` is declared as a plain storage variable at [1](#0-0)  and is only written inside `_updateRsETHPrice()`, which is invoked by the public, permissionless `updateRSETHPrice()` at [2](#0-1) 

The price is recomputed as `(totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)` at [3](#0-2)  — meaning it reflects the true backing ratio only at the moment of the call.

`depositETH()` and `depositAsset()` both call `_beforeDeposit()` → `getRsETHAmountToMint()`, which reads the stored price directly: [4](#0-3) 

Neither deposit function calls `updateRSETHPrice()` before this computation. [5](#0-4) 

`initiateWithdrawal()` calls `getExpectedAssetAmount()`, which also reads the stale stored price: [6](#0-5) 

**Why existing guards are insufficient:**
- The `minRSETHAmountExpected` slippage parameter in `depositETH`/`depositAsset` protects the depositor from receiving *too little* rsETH, but does nothing to prevent them from receiving *too much* due to a stale price.
- The `pricePercentageLimit` check inside `_updateRsETHPrice()` guards against a single large price jump during an update, but does not prevent the stored price from being stale between updates. In fact, if accumulated rewards push the price above the threshold, non-manager callers will receive a `PriceAboveDailyThreshold` revert, prolonging the staleness window. [7](#0-6) 

## Impact Explanation

**High — Theft of unclaimed yield.**

When staking rewards accrue, `totalETHInProtocol` increases but `rsETHPrice` remains at its last-updated (lower) value. A depositor exploiting this window receives:

```
rsethAmountToMint = (depositAmount × assetPrice) / staleLowerPrice
```

This is more rsETH than the true backing ratio warrants. The excess rsETH represents a direct claim on the yield that accrued to existing holders. When `updateRSETHPrice()` is eventually called, the new price is diluted by the inflated supply, and existing holders receive less yield than they earned. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation

**High.** No special privileges are required. Any external depositor can trigger this by calling `depositETH()` or `depositAsset()` during the window between reward accrual and the keeper's next `updateRSETHPrice()` call. On-chain reward accrual events (EigenLayer pod balance increases, stETH rebases) are publicly observable, enabling a sophisticated attacker to time deposits precisely. The attack is repeatable every reward cycle.

## Recommendation

Call `updateRSETHPrice()` (or an equivalent internal price refresh) at the start of `depositETH()`, `depositAsset()`, and `initiateWithdrawal()`, before any share or asset amount is computed. This ensures the exchange rate is always current at the point of use:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
```

Apply the same pattern to `depositAsset()` and `initiateWithdrawal()`.

## Proof of Concept

1. Protocol state: 1000 ETH in assets, 1000 rsETH supply → stored `rsETHPrice = 1.0 ETH/rsETH`.
2. 100 ETH in staking rewards accrue. True price = `1100/1000 = 1.1`. Stored price remains `1.0`.
3. Attacker calls `depositETH{value: 100 ETH}()` before the keeper calls `updateRSETHPrice()`.
4. `getRsETHAmountToMint` computes: `100e18 × 1e18 / 1.0e18 = 100 rsETH` minted. Correct amount at true price: `≈ 90.9 rsETH`. Attacker receives **~9.1 extra rsETH**.
5. Keeper calls `updateRSETHPrice()`. New supply = 1100 rsETH, total ETH = 1200 ETH. New price = `1200/1100 ≈ 1.0909`.
6. Attacker's 100 rsETH is worth `≈ 109 ETH` — a profit of ~9 ETH on a 100 ETH deposit.
7. Original 1000 rsETH holders now hold `1000/1100 × 1200 ≈ 1090.9 ETH` instead of the 1100 ETH they earned — losing ~9.1 ETH of yield to the attacker.

**Foundry fork test plan:** Fork mainnet, set `rsETHPrice` to a value below the current true backing ratio (simulate stale price by skipping a keeper update), call `depositETH` as an unprivileged address, then call `updateRSETHPrice()`, and assert that the attacker's rsETH balance × new price exceeds their deposit value, while existing holders' proportional ETH claim has decreased.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
