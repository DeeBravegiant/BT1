### Title
Missing Chainlink Staleness Checks Allow Stale/Invalid Price to Corrupt rsETH Minting Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `roundId`, `updatedAt`, and `answeredInRound`, performing zero staleness validation. A stale or incomplete Chainlink round feeds a corrupted asset price into the rsETH exchange rate calculation, causing over- or under-minting of rsETH for every depositor.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, the `getAssetPrice()` function reads from Chainlink but ignores all fields except `price`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

No check is made for:
- `answeredInRound >= roundId` — detects a carried-over (stale) answer
- `timestamp != 0` — detects an incomplete round
- `price > 0` — detects an invalid/negative price

The same codebase already implements all three checks correctly in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The vulnerable `getAssetPrice()` is consumed by `LRTOracle.getAssetPrice()`: [3](#0-2) 

Which is called inside `_getTotalEthInProtocol()` to sum all LST asset values: [4](#0-3) 

That total feeds `_updateRsETHPrice()`, which computes and stores the `rsETHPrice` used to determine how many rsETH tokens are minted per deposit: [5](#0-4) 

`updateRSETHPrice()` is a public, permissionless function callable by anyone: [6](#0-5) 

---

### Impact Explanation

**Stale low price scenario (`answeredInRound < roundId`):** A carried-over price that is lower than the true market price causes `_getTotalEthInProtocol()` to undercount TVL. The resulting `newRsETHPrice` is artificially depressed, so new depositors receive more rsETH than their deposit is worth. This dilutes all existing rsETH holders — a direct theft of yield from current holders.

**Incomplete round (`timestamp == 0`):** A zero timestamp means the round has not settled. The returned `price` is undefined/zero. `uint256(0)` propagates through the TVL sum, collapsing the computed rsETH price and causing massive over-minting for the next depositor.

**Negative price (`price <= 0`):** In Solidity 0.8, an explicit cast `uint256(negative_int256)` does not revert; it wraps to a huge value. This inflates the TVL sum astronomically, producing an rsETH price far above `highestRsethPrice`, which either reverts for non-managers or, if called by a manager, mints an unbounded protocol fee to the treasury.

---

### Likelihood Explanation

Chainlink feeds do occasionally experience incomplete rounds and stale carry-overs during network congestion or sequencer downtime (especially on L2s). The entry point `updateRSETHPrice()` is public and permissionless — any external actor can trigger it at the exact moment a stale round is active, making this reliably exploitable without any privileged access.

---

### Recommendation

Apply the same staleness guards already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(answeredInRound >= roundId, "Stale price");
    require(updatedAt != 0, "Incomplete round");
    require(price > 0, "Invalid price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

---

### Proof of Concept

1. A Chainlink LST/ETH feed enters an incomplete round (`timestamp == 0`) or carries over a stale answer (`answeredInRound < roundId`).
2. An unprivileged attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `LRTOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice(asset)`.
4. `latestRoundData()` returns a stale/zero price; no check reverts the call.
5. `_getTotalEthInProtocol()` returns a corrupted TVL.
6. `newRsETHPrice` is set to a value lower than the true rate.
7. The attacker (or any user) immediately calls `LRTDepositPool.depositAsset()`, receiving more rsETH than their deposit is worth at the true rate.
8. All pre-existing rsETH holders are diluted — their share of the underlying TVL is permanently reduced. [7](#0-6) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

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

**File:** contracts/LRTOracle.sol (L231-250)
```text
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

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
