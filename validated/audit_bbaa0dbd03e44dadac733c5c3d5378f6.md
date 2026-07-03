Audit Report

## Title
Missing L2 Sequencer Uptime Check Enables Stale Price Exploitation During Arbitrum Downtime - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

## Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` is deployed on Arbitrum and calls `latestRoundData()` without verifying the Arbitrum sequencer is live. During sequencer downtime, the oracle silently returns the last committed (stale) price, which passes all three existing guards. Any user can call `RSETHPool.deposit(token, amount, referralId)` during or immediately after downtime to receive over-minted rsETH relative to the actual collateral value deposited.

## Finding Description
`ChainlinkOracleForRSETHPoolCollateral.getRate()` performs three checks after calling `latestRoundData()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [1](#0-0) 

None of these detect sequencer downtime. When the Arbitrum sequencer is offline, L2 oracle update transactions are not processed. The last committed price remains in the feed with `answeredInRound >= roundID`, `timestamp != 0`, and `ethPrice > 0`, so all three guards pass and the stale price is returned as current.

`RSETHPool` is explicitly the Arbitrum pool: [2](#0-1) 

The stale rate flows directly into the rsETH minting formula via `viewSwapRsETHAmountAndFee`:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [3](#0-2) 

If the collateral token's price dropped during sequencer downtime, `tokenToETHRate` is inflated relative to the real market price, and the attacker receives more rsETH than the deposited collateral is worth. `ChainlinkPriceOracle.getAssetPrice()` has the same gap — it discards all five return values except `price` with no sequencer check: [4](#0-3) 

## Impact Explanation
An attacker depositing collateral at a stale inflated price receives excess rsETH. The pool's rsETH reserve (wrsETH held in `RSETHPool`) is drained at a rate exceeding the real collateral value deposited. This constitutes direct theft of funds from the pool's rsETH inventory. The impact maps to **High — Theft of unclaimed yield** (excess rsETH minted beyond what the deposited collateral warrants) and potentially **Critical — Direct theft of user funds** if the over-minted rsETH is redeemed against the pool's reserves.

## Likelihood Explanation
Arbitrum sequencer outages have occurred historically (e.g., December 2022). No special privilege is required — `deposit(address token, uint256 amount, string referralId)` is a public function callable by any EOA or contract. The only preconditions are sequencer downtime and an unfavorable price move during that window, both of which are realistic and externally observable. The attack is repeatable across multiple deposits within the same downtime window. [5](#0-4) 

## Recommendation
Add a sequencer uptime check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` following Chainlink's documented pattern for Arbitrum:

```solidity
AggregatorV3Interface internal sequencerUptimeFeed;
uint256 private constant GRACE_PERIOD = 3600; // 1 hour

function getRate() public view returns (uint256) {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (answer != 0) revert SequencerDown();
    if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();

    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();
    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    // ...
}
```

Apply the same fix to `ChainlinkPriceOracle.getAssetPrice()`.

## Proof of Concept
1. Arbitrum sequencer goes offline. Last committed wstETH/ETH Chainlink price: `1.15e18`.
2. Real market price of wstETH drops to `1.05e18` during the outage.
3. Sequencer comes back online; oracle has not yet been updated.
4. Attacker calls `RSETHPool.deposit(wstETH, 100e18, "")`.
5. `viewSwapRsETHAmountAndFee(100e18, wstETH)` calls `ChainlinkOracleForRSETHPoolCollateral.getRate()`.
6. `latestRoundData()` returns stale `1.15e18`; all three guards pass.
7. `rsETHAmount = 100e18 * 1.15e18 / rsETHToETHrate` — attacker receives rsETH priced at 1.15 ETH/wstETH while each wstETH is worth only 1.05 ETH.
8. Attacker gains ~9.5% excess rsETH per deposit, draining the pool's wrsETH reserve.

**Foundry fork test plan**: Fork Arbitrum mainnet at a block during a historical sequencer outage, mock the Chainlink wstETH/ETH feed to return a pre-downtime price, call `RSETHPool.deposit(wstETH, 100e18, "")`, and assert that `rsETHAmount > amountAfterFee * realPrice / rsETHToETHrate`. [1](#0-0)

### Citations

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

**File:** contracts/pools/RSETHPool.sol (L30-35)
```text
/// @title RSETHPool
/// @notice This contract is the pool contract for the rsETH pool on *Arbitrum*
/// @dev it differs from other RSETHPool contracts in other chains as it uses LZ_RSETH as the canonical rsETH token of
/// the chain.
/// @dev it was the first RSETHPool contract to be deployed in an L2 hence the legacy variables
contract RSETHPool is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/pools/RSETHPool.sol (L284-305)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPool.sol (L343-346)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```
