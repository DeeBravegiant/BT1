### Title
Live Oracle Call in `getExpectedAssetAmount` Blocks `initiateWithdrawal` and `instantWithdrawal` When Asset Price Oracle Reverts - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager.getExpectedAssetAmount()` makes a live call to `lrtOracle.getAssetPrice(asset)` on every invocation. This function is called inside both `initiateWithdrawal()` and `instantWithdrawal()`. If the underlying asset price oracle (e.g., Chainlink) reverts, all withdrawal initiation and instant withdrawal paths for that asset are permanently blocked until the oracle recovers, while `lrtOracle.rsETHPrice()` — used in the same formula — is a cached value unaffected by oracle downtime.

### Finding Description

`getExpectedAssetAmount` computes the asset payout for a given rsETH amount:

```solidity
// LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [1](#0-0) 

`lrtOracle.rsETHPrice()` is a **cached** state variable updated only when `updateRSETHPrice()` is explicitly called: [2](#0-1) 

`lrtOracle.getAssetPrice(asset)` is a **live** call that delegates to the registered price oracle for the asset:

```solidity
// LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
``` [3](#0-2) 

For example, `ChainlinkPriceOracle.getAssetPrice()` calls `priceFeed.latestRoundData()` directly with no fallback: [4](#0-3) 

`getExpectedAssetAmount` is called unconditionally inside both user-facing withdrawal entry points:

```solidity
// initiateWithdrawal — L168
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
``` [5](#0-4) 

```solidity
// instantWithdrawal — L228
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
``` [6](#0-5) 

The same inconsistency exists in `LRTDepositPool.getRsETHAmountToMint()`, which also calls `lrtOracle.getAssetPrice(asset)` live inside `depositETH` and `depositAsset`: [7](#0-6) 

### Impact Explanation

If the Chainlink feed (or any registered `IPriceFetcher`) for a supported asset reverts — due to sequencer downtime, feed deprecation, or oracle circuit-breaker — every call to `initiateWithdrawal` and `instantWithdrawal` for that asset reverts. Users holding rsETH who wish to exit into that asset are completely blocked from doing so for the duration of the outage. Because `rsETHPrice` is cached and unaffected, the protocol continues to function for other operations, but the withdrawal path for the affected asset is frozen.

**Impact: Medium — Temporary freezing of funds** (users cannot exit their rsETH position for the affected asset while the oracle is down).

### Likelihood Explanation

Chainlink feeds have historically paused or reverted during extreme market conditions (e.g., LUNA crash, ETH Merge). The protocol supports multiple LST assets (stETH, ETHx, sfrxETH, swETH), each with its own oracle. Any single oracle failure blocks the corresponding withdrawal path. No privileged action is required; the failure is triggered automatically by the oracle's own revert.

### Recommendation

Replace the live `getAssetPrice(asset)` call in `getExpectedAssetAmount` with a cached asset price, analogous to how `rsETHPrice` is already cached. Introduce a `mapping(address => uint256) public assetPrice` in `LRTOracle` that is updated alongside `rsETHPrice` in `_updateRsETHPrice()` / `_getTotalEthInProtocol()`. `getExpectedAssetAmount` and `getRsETHAmountToMint` should then read from this cached value rather than calling the live oracle, so that oracle downtime does not block user-facing withdrawal and deposit paths.

### Proof of Concept

1. Protocol has stETH as a supported asset with a Chainlink oracle registered in `LRTOracle`.
2. Chainlink's stETH/ETH feed reverts (e.g., sequencer down, feed deprecated).
3. User calls `initiateWithdrawal(stETH, rsETHAmount, "")`.
4. Execution reaches `getExpectedAssetAmount(stETH, rsETHAmount)` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → `priceFeed.latestRoundData()` → **reverts**.
5. The entire `initiateWithdrawal` transaction reverts. The user cannot queue a withdrawal.
6. `instantWithdrawal(stETH, rsETHAmount, "")` fails identically at line 228.
7. The user's rsETH is stuck in their wallet with no exit path for stETH until the oracle recovers. [8](#0-7) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-170)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
