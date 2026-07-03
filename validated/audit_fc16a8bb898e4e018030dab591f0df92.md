### Title
Single Reverting Asset Price Oracle Permanently Breaks `updateRSETHPrice()`, Freezing Protocol Fee Yield and Disabling Downside Protection - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._getTotalEthInProtocol()` iterates over every supported asset and calls `getAssetPrice(asset)` on each one with no `try/catch` guard. If any single asset's registered price oracle reverts — due to an upgrade, deprecation, hack, or self-destruct of the underlying token contract — the entire `updateRSETHPrice()` call reverts. This permanently freezes the rsETH price at its last stored value, halts protocol fee minting, and disables the automatic downside-protection pause mechanism.

---

### Finding Description

`LRTOracle._getTotalEthInProtocol()` is the private function that computes total ETH value across all supported assets. It is called unconditionally inside `_updateRsETHPrice()`, which is the shared internal path for both the public `updateRSETHPrice()` and the manager-gated `updateRSETHPriceAsManager()`.

```solidity
// contracts/LRTOracle.sol  lines 331-349
function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
    address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
    address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
    uint256 supportedAssetCount = supportedAssets.length;

    for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
        address asset = supportedAssets[assetIdx];
        uint256 assetER = getAssetPrice(asset);          // ← no try/catch
        uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
        totalETHInProtocol += totalAssetAmt.mulWad(assetER);
        unchecked { ++assetIdx; }
    }
}
```

`getAssetPrice` itself makes a raw external call:

```solidity
// contracts/LRTOracle.sol  line 156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);  // ← external call, no try/catch
}
```

Each price oracle plugin makes its own external call to the underlying token:

- `RETHPriceOracle.getAssetPrice` → `IrETH(rETHAddress).getExchangeRate()` (contracts/oracles/RETHPriceOracle.sol line 39)
- `SfrxETHPriceOracle.getAssetPrice` → `ISfrxETH(sfrxETHContractAddress).pricePerShare()` (contracts/oracles/SfrxETHPriceOracle.sol line 40)
- `ChainlinkPriceOracle.getAssetPrice` → `priceFeed.latestRoundData()` (contracts/oracles/ChainlinkPriceOracle.sol line 52)

If any one of these external calls reverts, the revert propagates up through `getAssetPrice` → `_getTotalEthInProtocol` → `_updateRsETHPrice` → `updateRSETHPrice` / `updateRSETHPriceAsManager`, making both entry points permanently uncallable.

The downside-protection logic that auto-pauses the protocol on a price drop lives entirely inside `_updateRsETHPrice` (lines 270-282 of LRTOracle.sol). If that function can never execute, the safety circuit never fires.

**Can the bad asset be removed?** `LRTConfig.removeSupportedAsset` (lines 66-94) does not call any oracle, so it is not blocked. However, it enforces:

```solidity
if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
    revert CannotRemoveAssetWithDeposits(asset);
}
```

Any live LST with meaningful deposits cannot be removed. The only unblocked admin escape hatch is `updatePriceOracleFor`, which replaces the oracle mapping without calling it. Until an admin acts, `updateRSETHPrice` is permanently broken.

---

### Impact Explanation

1. **Protocol fee minting is frozen.** `_updateRsETHPrice` is the sole path that mints rsETH as protocol fees (lines 299-308 of LRTOracle.sol). While the oracle is broken, all accrued yield that should be captured as fees is permanently lost for that period — matching **Medium: Permanent freezing of unclaimed yield**.

2. **Automatic downside-protection is disabled.** The mechanism that pauses `LRTDepositPool` and `LRTWithdrawalManager` when the rsETH price drops beyond `pricePercentageLimit` (lines 270-282 of LRTOracle.sol) never executes. A slashing event during this window would not trigger the safety pause, exposing depositors and withdrawers to incorrect exchange rates.

3. **rsETH price is stale.** `LRTDepositPool.getRsETHAmountToMint` (line 520) and `LRTWithdrawalManager.getExpectedAssetAmount` both read the stored `rsETHPrice`. A stale price means new depositors receive more rsETH than they should (diluting existing holders) and withdrawers receive incorrect asset amounts.

---

### Likelihood Explanation

The supported assets (stETH, ETHx, sfrxETH, rETH) each have their own oracle plugin that calls an external contract. Any of the following realistic events causes a permanent revert:

- An LST protocol upgrades its implementation and removes or renames the queried function (e.g., `getExchangeRate`, `pricePerShare`).
- A Chainlink feed is deprecated and its proxy self-destructs or begins reverting.
- An LST contract is frozen or paused for an extended period (e.g., emergency patch).
- A hack causes the underlying token contract to self-destruct.

These are the same categories of failure explicitly enumerated in the original Reserve Protocol report and are realistic for any live DeFi protocol.

---

### Recommendation

Wrap each external oracle call inside `_getTotalEthInProtocol` with a `try/catch` block. On failure, either skip the asset (using its last cached price) or revert with a descriptive error that identifies the failing asset, allowing governance to act on a specific oracle rather than having the entire price-update mechanism silently broken.

```solidity
// Suggested pattern inside the loop
try IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset) returns (uint256 price) {
    totalETHInProtocol += totalAssetAmt.mulWad(price);
} catch {
    // emit event or use last cached price; do not silently skip TVL
    revert OracleFailedForAsset(asset);
}
```

Additionally, store a per-asset cached price that can be used as a fallback when the live oracle reverts, so that a single bad oracle degrades gracefully rather than bricking the entire price-update path.

---

### Proof of Concept

1. Protocol supports three assets: stETH, ETHx, rETH.
2. The rETH token contract (`rETHAddress` in `RETHPriceOracle`) is upgraded and `getExchangeRate()` is removed.
3. Anyone calls `updateRSETHPrice()`.
4. Execution path: `updateRSETHPrice` → `_updateRsETHPrice` → `_getTotalEthInProtocol` → `getAssetPrice(rETH)` → `IPriceFetcher(rETHOracle).getAssetPrice(rETH)` → `IrETH(rETHAddress).getExchangeRate()` → **REVERT** (function does not exist).
5. The revert propagates all the way up; `updateRSETHPrice()` reverts.
6. `rsETHPrice` is now permanently stale. Protocol fees cannot be minted. The downside-protection pause cannot fire.
7. Admin cannot remove rETH from `supportedAssetList` because `getTotalAssetDeposits(rETH) > maxNegligibleAmount`.
8. Admin must call `updatePriceOracleFor(rETH, newOracle)` to unblock the system — but until that transaction is mined, all fee yield for the period is lost and the safety circuit is disabled. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L299-313)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;
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

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }
```
