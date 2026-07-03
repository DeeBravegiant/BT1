### Title
Chainlink `latestRoundData()` Called Without L2 Sequencer Uptime Check, Enabling Stale-Price Minting - (File: `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls `latestRoundData()` on a Chainlink feed deployed on L2 chains (Arbitrum, Optimism, Base, Scroll, Linea, etc.) without verifying that the L2 sequencer is live. When the sequencer is down, the feed returns a stale price that the pool treats as fresh, allowing depositors to mint wrsETH at an incorrect rate.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` fetches the collateral price via `AggregatorV3Interface(oracle).latestRoundData()` and performs three validity checks — `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` — but performs **no check on whether the L2 sequencer is operational**. [1](#0-0) 

On L2 chains, when the sequencer goes offline, Chainlink feeds stop updating but continue to return the last recorded answer. The three existing checks all pass on a stale-but-valid round, so `getRate()` returns a price that may be hours old.

This rate flows directly into `RSETHPoolV2.viewSwapRsETHAmountAndFee()`, which computes the wrsETH amount to mint: [2](#0-1) 

`rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` — a stale (lower) rate produces a larger `rsETHAmount`, minting more wrsETH than the deposited ETH actually backs.

The `deposit()` function is the public entry point that calls `viewSwapRsETHAmountAndFee` and then mints: [3](#0-2) 

No sequencer uptime check exists anywhere in the contract suite: [4](#0-3) 

---

### Impact Explanation

rsETH continuously accrues staking rewards, so its ETH-denominated value increases monotonically over time. When the sequencer is down for an extended period (hours), the stale rate is lower than the true current rate. A depositor calling `deposit()` during or immediately after sequencer downtime receives more wrsETH than the deposited ETH warrants, diluting the backing of all existing wrsETH holders. This constitutes **theft of unclaimed yield** from existing holders (High impact).

---

### Likelihood Explanation

Arbitrum, Optimism, Base, Scroll, and Linea have all experienced sequencer outages historically. The protocol is explicitly deployed on all of these chains (per README). The `deposit()` function is permissionless and callable by any user. An attacker monitoring sequencer status can time a deposit immediately after the sequencer resumes (while the feed has not yet updated) to exploit the stale price window.

---

### Recommendation

Add a sequencer uptime check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` using the Chainlink L2 sequencer uptime feed, following the [Chainlink documentation](https://docs.chain.link/data-feeds/l2-sequencer-feeds#example-code). Revert if the sequencer is down or if the grace period since recovery has not elapsed (typically 3600 seconds).

```solidity
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer == 1) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

---

### Proof of Concept

1. L2 sequencer (e.g., Arbitrum) goes offline. rsETH accrues staking rewards; its true ETH value increases from 1.05 ETH to 1.06 ETH over the outage period.
2. The Chainlink feed on Arbitrum is frozen at the pre-outage value of 1.05 ETH/rsETH.
3. Sequencer comes back online. The Chainlink feed has not yet updated.
4. Attacker calls `RSETHPoolV2.deposit{value: 1 ether}("")`.
5. `getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()` returns stale rate `1.05e18`.
6. `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.952 wrsETH` — but the correct amount at the true rate of `1.06e18` would be `≈ 0.943 wrsETH`.
7. Attacker receives ~0.009 excess wrsETH per ETH deposited, at the expense of existing pool participants. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L1-42)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.27;

interface AggregatorV3Interface {
    function decimals() external view returns (uint8);

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);
}

/// @title ChainlinkOracleForRSETHPoolCollateral Contract
/// @notice Wrapper contract for Chainlink oracles
contract ChainlinkOracleForRSETHPoolCollateral {
    address public immutable oracle;

    error StalePrice();
    error IncompleteRound();
    error InvalidPrice();

    constructor(address _oracle) {
        oracle = _oracle;
    }

    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }

    function rate() external view returns (uint256) {
        return getRate();
    }
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
