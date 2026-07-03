### Title
Single Reverting External Price Oracle Can DOS the Entire `updateRSETHPrice()` Process — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._getTotalEthInProtocol()` iterates over every supported asset and calls each asset's external price oracle without any error handling. If a single external oracle reverts (due to being paused, an interface change, or a protocol upgrade), the entire `updateRSETHPrice()` call reverts. This permanently freezes the rsETH price at a stale value, blocking protocol fee collection and causing all downstream consumers of `rsETHPrice` to operate on incorrect data.

---

### Finding Description

`LRTOracle._getTotalEthInProtocol()` loops over all supported assets and calls `getAssetPrice(asset)` for each:

```solidity
// contracts/LRTOracle.sol lines 336–348
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);          // external oracle call — no try/catch
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    unchecked { ++assetIdx; }
}
```

`getAssetPrice` delegates directly to the registered external oracle with no error handling:

```solidity
// contracts/LRTOracle.sol line 157
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
```

The protocol integrates multiple external LST price oracles:
- `EthXPriceOracle` → calls `IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).getExchangeRate()` (Stader)
- `RETHPriceOracle` → calls `IrETH(rETHAddress).getExchangeRate()` (Rocket Pool)
- `SfrxETHPriceOracle` → calls `ISfrxETH(sfrxETHContractAddress).pricePerShare()` (Frax)
- `ChainlinkPriceOracle` → calls `priceFeed.latestRoundData()` (Chainlink)

If any one of these external calls reverts, `_getTotalEthInProtocol()` reverts, `_updateRsETHPrice()` reverts, and `updateRSETHPrice()` reverts. The stored `rsETHPrice` is never updated.

---

### Impact Explanation

The stored `rsETHPrice` is consumed by:

1. **`LRTDepositPool.getRsETHAmountToMint`** — calculates how much rsETH to mint per deposit. A stale price means depositors receive incorrect rsETH amounts relative to actual TVL.
2. **`LRTWithdrawalManager.unlockQueue`** — uses `lrtOracle.rsETHPrice()` (the stored value) to compute how much asset to unlock per rsETH burned. A stale price desynchronizes the withdrawal queue.
3. **Protocol fee minting** — `_updateRsETHPrice()` mints protocol fees as rsETH on each call. If the function is DOSed, protocol fees are never collected (permanent freezing of unclaimed yield).
4. **Price-drop auto-pause** — the downside protection that pauses the protocol on a large price drop also lives inside `_updateRsETHPrice()`. If the function is DOSed, this safety mechanism never fires.

**Impact classification**: High — theft of unclaimed yield (protocol fee permanently not collected); Medium — temporary freezing of funds (stale price used for all deposits and withdrawal queue unlocking).

---

### Likelihood Explanation

The protocol integrates at least four distinct external LST protocols. Each of those protocols can independently:
- Be paused by their own governance or guardian
- Undergo an upgrade that temporarily breaks their interface
- Revert due to an internal invariant violation

The probability of at least one of N independent external protocols experiencing a transient revert grows with N. This is not a theoretical edge case — LST protocol pauses and upgrade-related reverts have occurred historically on mainnet. No attacker action is required; the DOS is triggered by any external protocol event.

---

### Recommendation

Wrap each external oracle call in a `try/catch` block inside `_getTotalEthInProtocol()`. On failure, either skip the asset (using its last known price as a fallback) or revert with a specific error that identifies the failing oracle, allowing the admin to temporarily remove it. This mirrors the pattern already used in `LRTWithdrawalManager.unlockQueue` for the Aave integration:

```solidity
// contracts/LRTWithdrawalManager.sol lines 311–316
try this.depositToAaveExternal(assetAmountUnlocked) { }
catch (bytes memory reason) {
    emit AaveDepositFailed(assetAmountUnlocked, reason);
}
```

Apply the same resilience to oracle calls in `_getTotalEthInProtocol()`.

---

### Proof of Concept

1. Protocol supports assets: `[stETH, ETHx, rETH, sfrxETH]`.
2. Stader (`IETHXStakePoolsManager`) pauses its `getExchangeRate()` function.
3. Anyone calls `LRTOracle.updateRSETHPrice()`.
4. Execution path: `updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → loop iteration for `ETHx` → `getAssetPrice(ETHx)` → `EthXPriceOracle.getAssetPrice(ETHx)` → `IETHXStakePoolsManager.getExchangeRate()` → **reverts**.
5. The entire call reverts. `rsETHPrice` is not updated.
6. Protocol fee is not minted. All deposits and withdrawal queue unlocks continue using the stale `rsETHPrice`.
7. The price-drop auto-pause safety mechanism is also silently disabled for the duration of the DOS.

**Relevant code locations**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/oracles/EthXPriceOracle.sol (L46-52)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != ethxAddress) {
            revert InvalidAsset();
        }

        return IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).getExchangeRate();
    }
```

**File:** contracts/oracles/RETHPriceOracle.sol (L34-40)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != rETHAddress) {
            revert InvalidAsset();
        }

        return IrETH(rETHAddress).getExchangeRate();
    }
```

**File:** contracts/oracles/SfrxETHPriceOracle.sol (L35-41)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != sfrxETHContractAddress) {
            revert InvalidAsset();
        }

        return ISfrxETH(sfrxETHContractAddress).pricePerShare();
    }
```
