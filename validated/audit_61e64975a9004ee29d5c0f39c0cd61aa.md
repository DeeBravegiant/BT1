Audit Report

## Title
Unchecked Chainlink `latestRoundData()` Return Values Enable Stale/Invalid Price Acceptance - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and consumes only the `answer` field, discarding `roundId`, `updatedAt`, and `answeredInRound`. A stale, zero, or negative price passes through unchecked. This contrasts directly with `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository, which validates all three conditions. The unchecked price feeds into rsETH mint calculations and the stored `rsETHPrice`, enabling depositors to receive more rsETH than their collateral warrants or to lose deposited funds entirely.

## Finding Description

**Root cause — `contracts/oracles/ChainlinkPriceOracle.sol` lines 52–54:**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values are available (`roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound`); only `answer` is used. No check for `answeredInRound < roundId` (stale round), `updatedAt == 0` (incomplete round), or `price <= 0` (invalid answer).

**Contrast with `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` lines 27–32**, which correctly validates all three before returning:

```solidity
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

**Exploit path A — stale inflated price, rsETHPrice not yet updated:**

1. stETH/ETH feed is current; `rsETHPrice` was last set correctly when stETH = 1.0 ETH.
2. stETH real price drops to 0.95 ETH; the Chainlink feed goes stale and still reports 1.0 ETH.
3. `updateRSETHPrice()` has not yet been called, so the stored `rsETHPrice` still reflects the correct 1.0 ETH era.
4. Attacker calls `depositAsset(stETH, amount, 0, "")`.
5. `getRsETHAmountToMint()` computes: `(amount × getAssetPrice(stETH)) / rsETHPrice` = `(amount × 1.0 ETH) / rsETHPrice`. The stale feed makes stETH appear worth 1.0 ETH when it is worth 0.95 ETH; the attacker receives ~5.3% more rsETH than their collateral warrants, diluting all existing holders.

**Exploit path B — zero price causes depositor fund loss:**

1. A deprecated or malfunctioning feed returns `price = 0`.
2. `getAssetPrice()` returns `uint256(0) * 1e18 / decimals = 0`.
3. `rsethAmountToMint = (amount × 0) / rsETHPrice = 0`.
4. `_beforeDeposit` only checks `rsethAmountToMint < minRSETHAmountExpected`; with `minRSETHAmountExpected = 0` (the default in the PoC), the check passes.
5. `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` executes; the user's tokens are taken but 0 rsETH is minted — direct fund loss.

**Exploit path C — zero price collapses rsETHPrice, enabling over-minting for other depositors:**

1. stETH feed returns 0; `_getTotalEthInProtocol()` computes stETH's contribution as 0.
2. `newRsETHPrice` drops sharply. If `pricePercentageLimit == 0`, no pause is triggered and `rsETHPrice` is set to this deflated value.
3. A depositor of a different asset (e.g., ETH) calls `depositAsset`; with a deflated `rsETHPrice` denominator, they receive far more rsETH than their ETH is worth — protocol insolvency.

**Existing guards are insufficient:**

- The `pricePercentageLimit` upside check (`LRTOracle.sol` lines 252–266) only fires when `newRsETHPrice > highestRsethPrice` and `pricePercentageLimit > 0`; it does not protect against a stale inflated price that is still below the historical peak.
- The downside pause (`LRTOracle.sol` lines 270–281) only fires when `pricePercentageLimit > 0` and the drop exceeds the limit; with `pricePercentageLimit == 0` it never fires.
- `minRSETHAmountExpected` is a user-supplied slippage parameter, not a protocol-level guard; passing 0 is the documented default in the PoC call.

## Impact Explanation

**Critical — Direct theft of user funds / Protocol insolvency.**

- Path B causes direct, irreversible loss of deposited ERC-20 tokens for any depositor who passes `minRSETHAmountExpected = 0` while the feed returns zero.
- Path C causes protocol insolvency: a zero price on one asset deflates `rsETHPrice`, allowing depositors of other assets to extract excess rsETH, permanently diluting all existing rsETH holders.
- Path A causes share/asset mis-accounting that accumulates into insolvency over repeated deposits during a stale-feed window.

## Likelihood Explanation

`updateRSETHPrice()` is a public, permissionless function callable by any address when the protocol is not paused. Chainlink feeds go stale during sequencer outages (L2), extreme volatility, or feed deprecation — no attacker action is required to create the condition. An attacker only needs to monitor the feed and call `depositAsset()` before `updateRSETHPrice()` is called with the corrected price. Path B and C require only a malfunctioning or deprecated feed, which has occurred historically. The exploit is repeatable every time the feed is stale.

## Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();
    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a `heartbeat`-based staleness check (`block.timestamp - updatedAt > maxStaleness`) per feed.

## Proof of Concept

**Path B (zero price → fund loss) — Foundry fork test outline:**

```solidity
// 1. Deploy mock Chainlink feed that returns price = 0
MockAggregator mockFeed = new MockAggregator(0, 8);
// 2. Set it as the price feed for stETH in ChainlinkPriceOracle
chainlinkOracle.updatePriceFeedFor(stETH, address(mockFeed)); // onlyLRTManager
// 3. Victim deposits stETH with minRSETHAmountExpected = 0
uint256 balanceBefore = stETH.balanceOf(victim);
vm.prank(victim);
lrtDepositPool.depositAsset(stETH, 1e18, 0, "");
// 4. Assert: stETH transferred out, rsETH balance = 0
assertEq(stETH.balanceOf(victim), balanceBefore - 1e18);
assertEq(rsETH.balanceOf(victim), 0); // funds lost
```

**Path A (stale price → over-minting) — Foundry fork test outline:**

```solidity
// 1. Fork mainnet; rsETHPrice is set correctly at block N (stETH = 1.0 ETH)
// 2. Advance time; mock feed still returns 1.0 ETH while real stETH = 0.95 ETH
// 3. Do NOT call updateRSETHPrice() (rsETHPrice still reflects 1.0 ETH era)
// 4. Attacker deposits stETH; getAssetPrice returns 1.0 ETH (stale)
// 5. rsethAmountToMint = (amount * 1.0e18) / rsETHPrice
//    vs. fair amount = (amount * 0.95e18) / rsETHPrice
// 6. Assert attacker received ~5.3% excess rsETH
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/LRTOracle.sol (L248-282)
```text

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
        }

        // downside protection — pause if price drops too far
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
