### Title
No L2 Sequencer Uptime Check in Chainlink Oracle Allows Stale-Price Over-Minting of rsETH - (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` on L2 chains without verifying that the L2 sequencer is live. If the sequencer goes offline, the oracle silently returns the last recorded (stale) price. Because rsETH is a yield-bearing token whose ETH-denominated price only increases over time, the stale price will be lower than the true current price, allowing any depositor to mint more rsETH than they are entitled to.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` performs basic sanity checks (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`) but contains **no L2 sequencer uptime check**: [1](#0-0) 

This oracle is wired as `rsETHOracle` in `RSETHPoolV2` and `RSETHPoolV3`, both of which are deployed on multiple L2 networks (Arbitrum, Optimism, Base, Scroll, Linea, zkSync, Mode, Blast, etc.): [2](#0-1) 

The deposit flow calls `getRate()` to compute how many rsETH tokens to mint: [3](#0-2) 

When the L2 sequencer is down, Chainlink's L2 data feeds stop updating but remain readable, returning the last price recorded before the outage. The existing checks (`answeredInRound`, `timestamp`) do **not** detect a sequencer outage — they only detect incomplete rounds or zero timestamps, neither of which occurs during a sequencer downtime.

The same pattern exists in `RSETHPoolV3`, which also calls `IOracle(rsETHOracle).getRate()` for both ETH and ERC-20 token deposits: [4](#0-3) 

A grep across the entire codebase confirms there is **no sequencer uptime feed check anywhere** in the production contracts.

Additionally, `ChainlinkPriceOracle.getAssetPrice()` on mainnet has no staleness check of any kind: [5](#0-4) 

---

### Impact Explanation

rsETH is a yield-bearing token; its ETH-denominated price (`rsETHToETHrate`) monotonically increases. When the sequencer goes offline, the stale price is lower than the true current price. The mint formula is:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate
```

A lower (stale) `rsETHToETHrate` produces a **higher** `rsETHAmount`. An attacker who deposits ETH during or immediately after a sequencer outage receives more rsETH than the deposited ETH is worth at the true current rate. This dilutes all existing rsETH holders and constitutes **theft of unclaimed yield** (High impact).

---

### Likelihood Explanation

L2 sequencer outages are documented historical events (Arbitrum, Optimism, and Base have each experienced outages). The protocol is deployed on at least eight L2 networks, multiplying exposure. No privileged access is required — any public depositor can call `deposit()` during or immediately after an outage. The window of exploitation lasts from the moment the sequencer resumes (transactions process again) until the oracle price catches up.

---

### Recommendation

Follow the [Chainlink L2 Sequencer Uptime Feeds](https://docs.chain.link/data-feeds/l2-sequencer-feeds) pattern. Add a sequencer uptime feed address to `ChainlinkOracleForRSETHPoolCollateral` and revert in `getRate()` if the sequencer is reported as down or if the grace period after recovery has not elapsed:

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

function getRate() public view returns (uint256) {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (answer != 0) revert SequencerDown();
    if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();

    // existing checks ...
}
```

Apply the same fix to `ChainlinkPriceOracle.getAssetPrice()` for any L2 deployments, and add a staleness threshold check (`block.timestamp - updatedAt > MAX_STALENESS`) to both oracles.

---

### Proof of Concept

1. Protocol is deployed on Arbitrum. `RSETHPoolV2.rsETHOracle` points to `ChainlinkOracleForRSETHPoolCollateral` wrapping the rsETH/ETH Chainlink feed.
2. True rsETH/ETH rate is `1.05e18`. Sequencer goes offline; last recorded Chainlink price is `1.04e18`.
3. Sequencer comes back online. Chainlink feed has not yet updated (still shows `1.04e18`). `answeredInRound == roundID` and `timestamp != 0`, so all existing checks pass.
4. Attacker calls `RSETHPoolV2.deposit{value: 100 ether}("")`.
5. `viewSwapRsETHAmountAndFee(100e18)` → `getRate()` returns `1.04e18`.
6. `rsETHAmount = 100e18 * 1e18 / 1.04e18 ≈ 96.15 rsETH` (correct at true rate: `100e18 / 1.05e18 ≈ 95.24 rsETH`).
7. Attacker receives ~0.91 excess rsETH per 100 ETH deposited, at the expense of existing rsETH holders.
8. At scale (daily mint limit), the over-minting can be significant. [1](#0-0) [6](#0-5)

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

**File:** contracts/pools/RSETHPoolV2.sol (L200-203)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-218)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-234)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L234-237)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
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
