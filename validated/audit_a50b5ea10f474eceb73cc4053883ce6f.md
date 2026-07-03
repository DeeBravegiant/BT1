Audit Report

## Title
Stale Cached `rsETHPrice` Allows Depositors to Mint Excess rsETH at an Outdated Exchange Rate — (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle` stores `rsETHPrice` as a plain storage variable updated only via explicit calls to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`. The deposit minting formula in `LRTDepositPool.getRsETHAmountToMint()` divides by this cached value while using a live asset price in the numerator. When staking rewards accrue inside EigenLayer strategies between price updates, the stale-low denominator causes the protocol to mint more rsETH than the deposited assets are worth, diluting the yield owed to existing rsETH holders.

## Finding Description

`rsETHPrice` is declared as a plain storage variable in `LRTOracle`:

```solidity
// contracts/LRTOracle.sol L28
uint256 public override rsETHPrice;
```

It is written only inside `_updateRsETHPrice()`, which is reached exclusively through two explicit entry-points — neither of which is called during the deposit flow:

```solidity
// contracts/LRTOracle.sol L87-96
function updateRSETHPrice() public whenNotPaused { _updateRsETHPrice(); }
function updateRSETHPriceAsManager() external onlyLRTManager { _updateRsETHPrice(); }
```

The deposit path is: `depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()`:

```solidity
// contracts/LRTDepositPool.sol L519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` reads a **live** price from the configured `IPriceFetcher` on every call:

```solidity
// contracts/LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
```

`lrtOracle.rsETHPrice()` returns the **cached** storage value. As EigenLayer strategies accrue staking rewards, the true rsETH/ETH rate rises continuously, but `rsETHPrice` remains at its last-written value until someone explicitly calls `updateRSETHPrice()`. During this window the denominator is too small, so the formula mints more rsETH than the deposited assets are worth.

`_updateRsETHPrice()` computes the correct new price from live data only at call time:

```solidity
// contracts/LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
// contracts/LRTOracle.sol L313
rsETHPrice = newRsETHPrice;
```

There is no mechanism that forces a price refresh before or during a deposit. The `minRSETHAmountExpected` slippage parameter in `depositAsset()` protects the depositor from receiving too little, not the protocol from minting too much.

The same stale `rsETHPrice` is also consumed by L2 pool contracts through `RSETHRateProvider.getLatestRate()`:

```solidity
// contracts/cross-chain/RSETHRateProvider.sol L27-29
function getLatestRate() public view override returns (uint256) {
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
```

which feeds `RSETHPoolV2.viewSwapRsETHAmountAndFee()` and `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee()`, propagating the stale rate to L2 swap calculations.

## Impact Explanation

When `rsETHPrice` is stale-low, a depositor receives more rsETH than their deposit is worth. After `updateRSETHPrice()` is eventually called, the new price is computed over the now-larger rsETH supply, so the price per token is lower than it would have been without the excess mint. Every pre-existing rsETH holder's share of the protocol TVL is diluted. This constitutes **theft of unclaimed yield** from existing holders — a **High** severity impact under the allowed scope.

## Likelihood Explanation

`updateRSETHPrice()` is not called automatically; it depends on off-chain keepers or manual invocation. Staking rewards accrue continuously inside EigenLayer strategies. Any gap between reward accrual and the next price update creates an exploitable window. Because `updateRSETHPrice()` is public, an attacker can also observe the mempool, wait for a large reward accrual event, and deposit before the keeper's update transaction is mined — without requiring any privileged access. The attack is repeatable every reward cycle. Likelihood is **Medium**.

## Recommendation

1. **Refresh price atomically on deposit**: call `_updateRsETHPrice()` (or an equivalent view-only computation of the current rate) inside `getRsETHAmountToMint()` so the minting formula always uses a freshly computed rate rather than the cached storage value.
2. **Alternatively**, compute `rsethAmountToMint` directly from `_getTotalEthInProtocol()` and `rsETH.totalSupply()` inline, bypassing the cached variable entirely.
3. **For L2 pools**, ensure the rate pushed by `RSETHRateProvider` is refreshed before each deposit window opens, or add a maximum-age check on the received rate.

## Proof of Concept

1. At time T₀, `rsETHPrice = 1.05e18` (last stored value). True value after reward accrual is `1.06e18`.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint` computes: `100e18 × 1e18 / 1.05e18 ≈ 95.24 rsETH`. At the true price the correct amount would be `100e18 × 1e18 / 1.06e18 ≈ 94.34 rsETH`. Attacker receives ~0.9 rsETH excess.
4. Anyone calls `updateRSETHPrice()`. The new price is computed over the enlarged supply, landing below `1.06e18`. Every pre-existing holder's rsETH is worth fractionally less than it should be.
5. Attacker repeats this every reward cycle, extracting yield continuously with no privileged access — only a public `depositAsset()` call.

**Foundry fork test plan**: Fork mainnet, record `rsETHPrice` and `rsETH.totalSupply()`. Simulate EigenLayer reward accrual (e.g., increase strategy shares). Call `depositAsset()` without calling `updateRSETHPrice()` first. Assert that `rsethAmountToMint` exceeds `(depositAmount × getAssetPrice(asset)) / computedLiveRsETHPrice`. Then call `updateRSETHPrice()` and assert the new `rsETHPrice` is lower than it would have been without the excess mint, confirming dilution of pre-existing holders.