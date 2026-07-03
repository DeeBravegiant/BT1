### Title
Missing Chainlink Price Feed Staleness Check Allows Stale Prices to Drive rsETH Minting — (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp, performing no staleness validation. A stale LST price propagates through `LRTOracle._getTotalEthInProtocol()` into `_updateRsETHPrice()`, corrupting the rsETH/ETH exchange rate used for all deposits. When the stale price is below the true market price, the protocol mints excess rsETH for depositors, diluting existing holders and constituting theft of unclaimed yield.

---

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` destructures the five return values of `latestRoundData()` but binds only `price`, leaving `updatedAt` and `answeredInRound` completely unused:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

There is no comparison of `updatedAt` against `block.timestamp` and no heartbeat threshold check. The function returns whatever the last recorded answer is, regardless of how old it is.

This price is consumed by `LRTOracle._getTotalEthInProtocol()`, which sums `assetER * totalAssetAmt` across all supported LSTs:

```solidity
uint256 assetER = getAssetPrice(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [2](#0-1) 

The result feeds directly into `_updateRsETHPrice()`, which computes and stores the new `rsETHPrice`:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [3](#0-2) 

`updateRSETHPrice()` is a public, permissionless function — any caller can trigger a price update at any time:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [4](#0-3) 

The stored `rsETHPrice` is then used by `LRTDepositPool` to determine how many rsETH tokens to mint per unit of deposited LST. A stale-low price (LST appears cheaper than it is) causes `totalETHInProtocol` to be understated, depressing `rsETHPrice`, which in turn causes the deposit pool to mint more rsETH per deposited LST than the true exchange rate warrants.

---

### Impact Explanation
**High — Theft of unclaimed yield.**

When a supported LST's Chainlink feed goes stale at a value below the true market price (e.g., during oracle downtime or network congestion), `rsETHPrice` is set too low. Any depositor who calls `deposit()` on `LRTDepositPool` while this stale price is active receives more rsETH than their deposit is worth in ETH terms. This excess rsETH is backed by no additional ETH, diluting the share value of all existing rsETH holders — equivalent to stealing their accrued yield. The effect is permanent: once the excess rsETH is minted and the price corrects, existing holders' redemption value is permanently reduced.

---

### Likelihood Explanation
**Medium.** Chainlink feeds have documented heartbeat intervals (e.g., 24 hours for some LST/ETH feeds). During periods of network congestion, oracle keeper failures, or L2 sequencer downtime, feeds can remain stale for hours. Because `updateRSETHPrice()` is permissionless, an attacker can monitor for a stale feed and immediately call it to lock in the corrupted price before the feed recovers. No privileged access is required.

---

### Recommendation
Add a staleness check in `ChainlinkPriceOracle.getAssetPrice()` using the `updatedAt` return value from `latestRoundData()`. Compare it against a configurable heartbeat threshold (e.g., 25 hours for daily-heartbeat feeds):

```solidity
(, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePriceFeed();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `STALENESS_THRESHOLD` should be set per-asset to match each feed's documented heartbeat. Additionally, validate that `price > 0` to guard against a zero/negative answer.

---

### Proof of Concept

1. Assume `stETH` is a supported asset with a Chainlink feed that last updated 30 hours ago at `1.00 ETH` (true current price: `1.05 ETH`).
2. Attacker calls `updateRSETHPrice()`. `_getTotalEthInProtocol()` uses the stale `1.00 ETH` rate, understating TVL by ~5%.
3. `rsETHPrice` is set ~5% lower than the true value.
4. Attacker deposits `1000 stETH` via `LRTDepositPool`. At the deflated rsETH price, they receive ~5% more rsETH than the deposit is worth.
5. When the Chainlink feed updates and `rsETHPrice` corrects upward, the attacker's excess rsETH is now fully backed — the shortfall is borne by all existing rsETH holders whose redemption value has been diluted. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-250)
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

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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
