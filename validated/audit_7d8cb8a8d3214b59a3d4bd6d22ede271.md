Audit Report

## Title
Stale `rsETHPrice` Mixed with Live Chainlink Asset Price in Mint/Withdrawal Calculations Enables Yield Theft — (File: `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTDepositPool.getRsETHAmountToMint()` divides a live Chainlink asset price by a stored, periodically-updated `rsETHPrice`. When staking rewards have accrued but `updateRSETHPrice()` has not yet been called, the stored price is lower than the true price, causing depositors to receive more rsETH than their deposit is worth. This dilutes all existing rsETH holders by transferring their unclaimed yield to the new depositor. The same mismatch exists in `LRTWithdrawalManager.getExpectedAssetAmount()`.

## Finding Description

`getRsETHAmountToMint()` in `LRTDepositPool.sol` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.getAssetPrice(asset)` calls through to `ChainlinkPriceOracle.getAssetPrice()`, which reads `latestRoundData()` — a live, current-block value: [2](#0-1) 

`lrtOracle.rsETHPrice()` returns the stored state variable last written by `_updateRsETHPrice()`: [3](#0-2) 

`rsETHPrice` is only updated when `updateRSETHPrice()` or `updateRSETHPriceAsManager()` is explicitly called — it is not updated atomically with deposits: [4](#0-3) 

The same mismatch appears in `getExpectedAssetAmount()`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [5](#0-4) 

The staleness window is extended by the `pricePercentageLimit` guard: when the true price increase exceeds the configured threshold, non-manager callers of `updateRSETHPrice()` revert with `PriceAboveDailyThreshold`, leaving only managers able to update the price via `updateRSETHPriceAsManager()`: [6](#0-5) 

This means the staleness window is longest precisely when the mismatch is most profitable to exploit (large reward accrual events).

The `_updateRsETHPrice()` function computes the new price as `(totalETHInProtocol - protocolFeeInETH) / rsethSupply`, where both inputs are read live at call time: [7](#0-6) 

Any deposit made between reward accrual and the next price update inflates the rsETH supply at the stale (lower) price, permanently diluting the per-token value for all existing holders when the price is eventually updated.

The `minRSETHAmountExpected` slippage parameter in `depositETH`/`depositAsset` protects the depositor from receiving *fewer* rsETH than expected, but does not prevent the depositor from receiving *more* rsETH than their deposit is worth at the true price — which is the attack vector here. [8](#0-7) 

## Impact Explanation

**High — Theft of unclaimed yield.**

When staking rewards have accrued and `staleRsETHPrice < trueRsETHPrice`, a depositor receives excess rsETH. When `updateRSETHPrice()` is subsequently called, the new price is computed over the inflated supply, permanently reducing the per-token value for all pre-existing holders. The yield they earned is transferred to the attacker. This is a concrete, quantifiable, irreversible loss of accrued yield for existing rsETH holders, matching the "Theft of unclaimed yield" impact class.

## Likelihood Explanation

The condition is continuously present: staking rewards accrue every block, and `updateRSETHPrice()` is called periodically by off-chain bots, not atomically with every deposit. Any deposit in the interval between reward accrual and the next price update exploits the mismatch. The `pricePercentageLimit` mechanism — intended as a safety guard — actively extends the staleness window for large reward events, making the most profitable attack windows also the longest. No special privileges are required; `depositETH` and `depositAsset` are public functions callable by any EOA or contract.

## Recommendation

Compute `rsETHPrice` on-the-fly within `getRsETHAmountToMint` and `getExpectedAssetAmount` using the current TVL and current rsETH supply (mirroring the logic in `_getTotalEthInProtocol()` and `_updateRsETHPrice()`), rather than reading the stored `rsETHPrice`. Alternatively, call `_updateRsETHPrice()` internally at the start of every deposit and withdrawal that uses `rsETHPrice` in its calculation, ensuring both data sources are always from the same state.

## Proof of Concept

**Setup:**
- 1000 rsETH outstanding; stored `rsETHPrice = 1.0e18`; true TVL = 1100 ETH (100 ETH staking rewards accrued, price not yet updated)
- `pricePercentageLimit = 5e16` (5%). True price increase = 10%, so `updateRSETHPrice()` reverts for non-managers

**Attack sequence:**
1. Attacker calls `depositETH{value: 110 ether}(0, "")`.
2. `getRsETHAmountToMint` computes: `110e18 * 1e18 / 1e18 = 110 rsETH` (at stale price 1.0). At the true price of 1.1, the attacker should receive only 100 rsETH.
3. Manager calls `updateRSETHPriceAsManager()`. New TVL = 1210 ETH, new supply = 1110 rsETH. New price = `1210e18 / 1110 ≈ 1.0901e18` (diluted from the true 1.1).
4. Attacker holds 110 rsETH × 1.0901 ≈ 119.9 ETH value on a 110 ETH deposit — a ~9.9 ETH gain.
5. Original 1000 rsETH holders now hold 1000 × 1.0901 = 1090.1 ETH value instead of the 1100 ETH they were owed — a ~9.9 ETH loss of their accrued yield.

**Foundry fork test plan:**
```solidity
function testYieldTheftViaStaleRsETHPrice() public {
    // Fork mainnet at a block where rewards have accrued since last updateRSETHPrice
    // 1. Record existing holders' rsETH balance and current rsETHPrice
    // 2. Confirm updateRSETHPrice() reverts (pricePercentageLimit exceeded)
    // 3. Attacker calls depositETH with value = X ETH
    // 4. Record rsETH minted to attacker
    // 5. Manager calls updateRSETHPriceAsManager()
    // 6. Assert attacker's rsETH * newPrice > X ETH (profit)
    // 7. Assert existing holders' rsETH * newPrice < rsETH * expectedTruePrice (loss)
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
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

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
