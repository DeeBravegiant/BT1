### Title
`LRTOracle._getTotalEthInProtocol` Loop Breaks When Any Asset Price Oracle Reverts, Freezing rsETH Price Updates - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._getTotalEthInProtocol()` iterates over all supported assets and calls `getAssetPrice(asset)` for each one. If any single asset's external price oracle reverts, the entire loop reverts, causing `updateRSETHPrice()` to revert. There is no try-catch or error isolation. This permanently blocks rsETH price updates and protocol fee minting for as long as any one oracle is unhealthy.

---

### Finding Description

`_getTotalEthInProtocol()` is a private function called unconditionally by `_updateRsETHPrice()`, which is in turn called by the public `updateRSETHPrice()` and the manager-gated `updateRSETHPriceAsManager()`.

```solidity
// contracts/LRTOracle.sol lines 336–348
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);          // ← external call, no try-catch
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    unchecked { ++assetIdx; }
}
```

`getAssetPrice` delegates to an external `IPriceFetcher`:

```solidity
// contracts/LRTOracle.sol lines 156–158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
```

Chainlink-backed `IPriceFetcher` implementations can revert under well-known conditions: sequencer downtime on L2, stale round data, circuit-breaker triggers, or oracle contract upgrades. If any one of the N supported assets has a reverting oracle, the entire for-loop reverts, and `updateRSETHPrice()` becomes uncallable.

---

### Impact Explanation

When `updateRSETHPrice()` is broken:

1. **Protocol fee minting is frozen.** `_updateRsETHPrice()` is the only code path that mints protocol fees (lines 299–308). No fee can be minted until the oracle recovers. This constitutes **permanent freezing of unclaimed yield** for the duration of the outage.
2. **Price protection cannot trigger.** The downside-protection logic that pauses the protocol on a large price drop (lines 270–282) also lives inside `_updateRsETHPrice()`. It cannot execute.
3. **rsETH price becomes stale.** Deposits and withdrawals continue using the last stored `rsETHPrice`, which diverges from true NAV.

---

### Likelihood Explanation

The protocol supports multiple LST assets (stETH, ETHx, rETH, sfrxETH). Each has its own Chainlink-backed oracle. Chainlink oracles are known to revert or return stale data during:
- L2 sequencer downtime (Arbitrum, Optimism, Base — all chains where this protocol operates)
- Heartbeat gaps during low-volatility periods
- Oracle contract migrations

A single unhealthy oracle among N assets is sufficient to break the entire price update loop. The more assets are supported, the higher the probability of at least one oracle being temporarily unhealthy.

---

### Recommendation

Wrap the `getAssetPrice(asset)` call inside the loop with a try-catch (or a low-level `staticcall`). If an oracle reverts for a given asset, skip that asset's contribution or revert with a targeted error rather than propagating the revert through the entire loop. This mirrors the fix applied in the referenced Bond Protocol commit.

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    try IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset) returns (uint256 assetER) {
        uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
        totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    } catch {
        revert AssetOracleFailed(asset); // or skip, depending on desired semantics
    }
    unchecked { ++assetIdx; }
}
```

---

### Proof of Concept

1. Protocol has two supported assets: `stETH` (oracle healthy) and `ETHx` (oracle reverts due to sequencer downtime).
2. Anyone calls `updateRSETHPrice()`.
3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`.
4. Loop iteration for `stETH` succeeds.
5. Loop iteration for `ETHx` calls `getAssetPrice(ETHx)` → `IPriceFetcher(ethxOracle).getAssetPrice(ETHx)` → **reverts**.
6. The revert propagates up through `_getTotalEthInProtocol()` → `_updateRsETHPrice()` → `updateRSETHPrice()`.
7. `updateRSETHPrice()` reverts. The rsETH price is not updated. Protocol fees are not minted. The price protection mechanism cannot trigger.
8. This persists for the entire duration of the ETHx oracle outage.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L299-311)
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
