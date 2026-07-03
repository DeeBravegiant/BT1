### Title
Missing Chainlink Staleness Check Enables Stale Price Exploitation via Block Stuffing — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` performs three validation checks on Chainlink `latestRoundData()` output but omits any time-based staleness guard (`block.timestamp - updatedAt <= heartbeat`). On a low-throughput L2 such as Unichain — an explicitly supported deployment target — an attacker can perform block stuffing to prevent Chainlink heartbeat updates, causing `getRate()` to return a price up to 24 hours stale while passing all three existing checks. The stale inflated price is then consumed by `RSETHPoolNoWrapper.deposit(token, amount, referralId)`, allowing the attacker to receive more rsETH than the deposited collateral is worth at current market price.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls `latestRoundData()` and applies exactly three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();   // round completeness
if (timestamp == 0) revert IncompleteRound();          // round started
if (ethPrice <= 0) revert InvalidPrice();              // positive price
```

None of these checks whether the price is recent. A completed round with a non-zero timestamp and a positive price passes all three checks regardless of how old it is. The standard Chainlink best-practice guard — `require(block.timestamp - updatedAt <= heartbeat)` — is entirely absent. [1](#0-0) 

`RSETHPoolNoWrapper` is explicitly documented as the pool for chains including Unichain: [2](#0-1) 

Its `deposit(token, amount, referralId)` function calls `viewSwapRsETHAmountAndFee(amount, token)`, which fetches `tokenToETHRate` directly from the collateral oracle:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [3](#0-2) 

The computed `rsETHAmount` is then transferred directly from the pool's rsETH reserves to the caller: [4](#0-3) 

If `tokenToETHRate` is inflated (stale high price), the attacker receives more rsETH than the deposited collateral is worth at current fair value, draining the pool's rsETH reserves.

---

### Impact Explanation

An attacker who successfully stuffs blocks on Unichain for ~24 hours (the wstETH/ETH Chainlink heartbeat) can deposit wstETH at a stale inflated price and extract rsETH from the pool's reserves proportional to the price deviation. The pool's rsETH balance is depleted beyond what the deposited collateral backs, violating the invariant that every rsETH in the pool is fully collateralized. This constitutes theft of rsETH from pool reserves.

**Scoped impact: Low. Block stuffing.**

---

### Likelihood Explanation

Block stuffing on Unichain is expensive but feasible for a well-capitalized attacker if the profit from the stale-price arbitrage exceeds the cost of filling blocks for the heartbeat window. The wstETH/ETH feed has a 24-hour heartbeat, meaning the attacker must sustain block stuffing for nearly a full day — a high cost that limits likelihood. However, the missing staleness check is a permanent, unconditional code defect that also exposes the protocol to natural oracle staleness (Chainlink node outages, network congestion) without any attacker involvement.

---

### Recommendation

Add a configurable heartbeat/staleness check in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
uint256 public immutable heartbeat; // set in constructor, e.g. 86400 for 24h

constructor(address _oracle, uint256 _heartbeat) {
    oracle = _oracle;
    heartbeat = _heartbeat;
}

function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    if (block.timestamp - timestamp > heartbeat) revert StalePrice(); // ADD THIS

    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
```

---

### Proof of Concept

Fork test on Unichain (or local fork simulating Unichain block times):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";

interface IChainlinkOracle {
    function getRate() external view returns (uint256);
}

interface IPool {
    function deposit(address token, uint256 amount, string memory referralId) external;
    function rsETH() external view returns (address);
}

contract BlockStuffingPoC is Test {
    // Addresses (replace with actual Unichain deployment addresses)
    address constant POOL = address(0xPOOL);
    address constant ORACLE = address(0xORACLE); // ChainlinkOracleForRSETHPoolCollateral
    address constant WSTETH = address(0xWSTETH);

    function testStaleOracleDeposit() public {
        // 1. Record the current oracle price (assume wstETH/ETH = 1.15e18, fair value)
        uint256 stalePriceBeforeWarp = IChainlinkOracle(ORACLE).getRate();

        // 2. Simulate 23h59m passing without a Chainlink update (block stuffing scenario)
        vm.warp(block.timestamp + 23 hours + 59 minutes);

        // 3. Oracle still returns the same stale price — no revert
        uint256 stalePriceAfterWarp = IChainlinkOracle(ORACLE).getRate();
        assertEq(stalePriceBeforeWarp, stalePriceAfterWarp, "Oracle should return stale price");

        // 4. Assume market price has dropped to 1.10e18 (stale price is inflated by ~4.5%)
        // Attacker deposits 1 wstETH at stale price
        uint256 depositAmount = 1e18;
        deal(WSTETH, address(this), depositAmount);
        IERC20(WSTETH).approve(POOL, depositAmount);

        address rsETH = IPool(POOL).rsETH();
        uint256 rsETHBefore = IERC20(rsETH).balanceOf(address(this));

        IPool(POOL).deposit(WSTETH, depositAmount, "poc");

        uint256 rsETHReceived = IERC20(rsETH).balanceOf(address(this)) - rsETHBefore;

        // 5. Assert attacker received rsETH exceeding fair value
        // At fair price 1.10e18 wstETH/ETH and rsETH/ETH ~1.05e18:
        // fair rsETH = 1e18 * 1.10e18 / 1.05e18 ≈ 1.0476e18
        // stale rsETH = 1e18 * 1.15e18 / 1.05e18 ≈ 1.0952e18
        uint256 fairRsETHAmount = depositAmount * 1.10e18 / 1.05e18;
        assertGt(rsETHReceived, fairRsETHAmount, "Attacker received excess rsETH from stale price");
    }
}
```

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L27-30)
```text
/// @title RSETHPoolNoWrapper
/// @notice This contract is the deposit pool for the chains where there is no rsETH wrapper contract (e.g. Arbitrum,
/// Unichain)
contract RSETHPoolNoWrapper is AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L260-271)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-312)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
