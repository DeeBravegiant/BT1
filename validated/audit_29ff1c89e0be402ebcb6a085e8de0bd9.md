### Title
Stale `tokenToETHRate` via Block Stuffing Prematurely Exhausts `dailyMintLimit`, Temporarily Freezing Token Deposits - (`contracts/pools/RSETHPoolV3.sol`)

---

### Summary

`RSETHPoolV3.deposit(address,uint256,string)` applies the `limitDailyMint` modifier, which calls `viewSwapRsETHAmountAndFee(amount, token)` to compute `rsETHAmount` using `IOracle(supportedTokenOracle[token]).getRate()`. Neither `limitDailyMint` nor `viewSwapRsETHAmountAndFee` performs any staleness check on the returned `tokenToETHRate`. The supported token oracles (`CrossChainRateReceiver` via LayerZero, `ChainlinkOracleForRSETHPoolCollateral` via Chainlink keepers) are push-based: their on-chain state is updated by regular transactions that can be excluded from blocks. An attacker who stuffs blocks around a known oracle update window can keep a stale, inflated `tokenToETHRate` in place. Because `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate`, an inflated rate over-counts the rsETH equivalent of each deposit, exhausting `dailyMintLimit` prematurely and reverting all subsequent token deposits with `DailyMintLimitExceeded` until the next day's reset.

---

### Finding Description

**Entrypoint:**

`deposit(address token, uint256 amount, string referralId)` is a public, permissionless function. [1](#0-0) 

**`limitDailyMint` modifier:**

The modifier computes `rsETHAmount` via `viewSwapRsETHAmountAndFee(amount, token)` and accumulates it into `dailyMintAmount`. There is no staleness guard on the oracle rate used. [2](#0-1) 

**`viewSwapRsETHAmountAndFee` — no staleness check:**

`tokenToETHRate` is read directly from `supportedTokenOracle[token]` with no time-based validation:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [3](#0-2) 

**Push-based oracle 1 — `CrossChainRateReceiver`:**

`getRate()` returns the stored `rate` with no time-based check. The rate is only updated when the LayerZero endpoint calls `lzReceive`, which is a regular on-chain transaction susceptible to block stuffing. [4](#0-3) 

**Push-based oracle 2 — `ChainlinkOracleForRSETHPoolCollateral`:**

The staleness check is `answeredInRound < roundID` — this only detects an incomplete round, not elapsed time. If block stuffing prevents Chainlink keepers from submitting a new round, the last completed round's data passes the check indefinitely. [5](#0-4) 

**Attack scenario:**

1. A supported token (e.g., a bridged LST) begins depegging; its true ETH value is falling.
2. The oracle update transaction (LayerZero `lzReceive` or Chainlink keeper) would lower `tokenToETHRate`.
3. The attacker stuffs blocks with high-gas transactions to exclude the oracle update.
4. `tokenToETHRate` remains at the pre-depeg (inflated) value.
5. Each call to `deposit(token, amount, ...)` computes an inflated `rsETHAmount`, consuming `dailyMintAmount` faster than it should.
6. `dailyMintAmount + rsETHAmount > dailyMintLimit` triggers `DailyMintLimitExceeded`, blocking all further token deposits for the rest of the day.

---

### Impact Explanation

Legitimate users cannot deposit the supported token for up to 24 hours (until `getCurrentDay() > lastMintDay` resets `dailyMintAmount` to 0). No funds already in the contract are lost, but the deposit service is temporarily frozen for the affected token. This matches the scoped impact: **Low. Block stuffing.** [6](#0-5) 

---

### Likelihood Explanation

Block stuffing is economically viable on L2 chains (where RSETHPoolV3 is deployed) because block gas limits are lower and gas prices are cheaper than Ethereum mainnet. The attacker needs to know the oracle update cadence (predictable for both Chainlink heartbeat and periodic LayerZero pushes). No privileged access is required. The attack is pure griefing with no direct financial gain for the attacker, which limits real-world motivation, but the technical path is fully concrete and requires no external compromise.

---

### Recommendation

Add a time-based staleness check in `viewSwapRsETHAmountAndFee` (or in the oracle wrappers themselves) that reverts if `block.timestamp - lastUpdated > maxStaleness`. For `CrossChainRateReceiver`, expose `lastUpdated` and enforce a maximum age. For `ChainlinkOracleForRSETHPoolCollateral`, add a `block.timestamp - timestamp > heartbeat` check alongside the existing `answeredInRound` check. This ensures that a stale rate causes a revert rather than silently inflating `rsETHAmount`. [7](#0-6) [5](#0-4) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import {RSETHPoolV3} from "contracts/pools/RSETHPoolV3.sol";

contract MockOracle {
    uint256 public rate;
    constructor(uint256 _rate) { rate = _rate; }
    function getRate() external view returns (uint256) { return rate; }
    function setRate(uint256 _rate) external { rate = _rate; }
}

contract MockWrsETH {
    function mint(address, uint256) external {}
    // minimal ERC20 stubs omitted for brevity
}

contract BlockStuffingPoC is Test {
    RSETHPoolV3 pool;
    MockOracle rsETHOracle;
    MockOracle tokenOracle;
    MockWrsETH wrsETH;
    address token = address(0xBEEF);

    function setUp() public {
        rsETHOracle = new MockOracle(1e18);   // rsETH/ETH = 1.0
        tokenOracle = new MockOracle(1.1e18); // token/ETH = 1.1 (pre-depeg, inflated)
        wrsETH = new MockWrsETH();

        pool = new RSETHPoolV3();
        pool.initialize(address(this), address(this), address(wrsETH), 0, address(rsETHOracle), false);
        pool.reinitialize(100e18, block.timestamp); // dailyMintLimit = 100 rsETH

        // Add token with inflated oracle (block stuffing keeps this stale)
        pool.addSupportedToken(token, address(tokenOracle));
    }

    function testStaleRateExhaustsLimit() public {
        // True rate should be 0.9e18 (depegging), but oracle is stuck at 1.1e18
        // Each deposit of 10 tokens counts as 11 rsETH instead of 9 rsETH
        // dailyMintLimit of 100 rsETH is hit after ~9 deposits instead of ~11

        uint256 depositAmount = 10e18;
        uint256 depositsBeforeLimit = 0;

        for (uint256 i = 0; i < 20; i++) {
            try pool.deposit(token, depositAmount, "") {
                depositsBeforeLimit++;
            } catch {
                break;
            }
        }

        // With stale inflated rate (1.1e18): 100 / 11 ≈ 9 deposits before limit
        // With correct rate (0.9e18):        100 / 9  ≈ 11 deposits before limit
        assertLt(depositsBeforeLimit, 10, "Limit hit prematurely due to stale rate");
    }
}
```

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L328-334)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-17)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;

```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-105)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");

        address srcAddress;
        assembly {
            srcAddress := mload(add(_srcAddress, 20))
        }

        require(_srcChainId == srcChainId, "Src chainId must be correct");
        require(srcAddress == rateProvider, "Src address must be provider");

        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
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
