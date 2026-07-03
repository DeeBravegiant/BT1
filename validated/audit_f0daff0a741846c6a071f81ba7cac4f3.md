Audit Report

## Title
Stale `CrossChainRateReceiver` Rate With No Staleness Enforcement Enables Over-Minting of rsETH on L2 Deposits — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver` stores `lastUpdated` but `getRate()` returns the cached `rate` with no time-based staleness check. L2 pool contracts (`RSETHPool`, `RSETHPoolV3`, `RSETHPoolNoWrapper`) use this potentially stale rsETH/ETH rate alongside a live Chainlink collateral rate in the same pricing formula. Because rsETH is a monotonically appreciating token, a stale (lower) rsETH rate causes the formula to over-mint rsETH to depositors, extracting accrued yield from existing holders.

## Finding Description

`CrossChainRateReceiver` records `lastUpdated` on every `lzReceive` call but never enforces it in `getRate()`:

```solidity
// CrossChainRateReceiver.sol L95-104
rate = _rate;
lastUpdated = block.timestamp;   // stored but never checked

function getRate() external view returns (uint256) {
    return rate;                 // no staleness guard
}
``` [1](#0-0) 

The rate is only refreshed when someone calls `MultiChainRateProvider.updateRate()` on L1 and the LayerZero message is delivered. There is no on-chain heartbeat or forced-update mechanism. [2](#0-1) 

All three L2 pool contracts use `getRate()` (which delegates to `CrossChainRateReceiver.getRate()`) for the rsETH/ETH rate, while the collateral token rate is fetched live from Chainlink via `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
// RSETHPoolV3.sol L328-334
uint256 rsETHToETHrate = getRate();                                    // stale cached rate
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // live Chainlink
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [3](#0-2) [4](#0-3) [5](#0-4) 

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls `latestRoundData()` on every invocation and only checks `answeredInRound < roundID` (Chainlink's own round-completeness check), not a time-based staleness window: [6](#0-5) 

Because rsETH accrues yield monotonically, a stale `rsETHToETHrate` is always lower than the true current rate. The division `amountAfterFee * tokenToETHRate / rsETHToETHrate` therefore produces a larger `rsETHAmount` than the deposited collateral is actually worth. The deficit is borne by all existing rsETH/wrsETH holders when the pool's collected collateral is bridged to L1 and deposited at the true (higher) rate.

The `dailyMintLimit` in `RSETHPoolV3` caps total daily minting volume but does not prevent mispriced minting within that cap. [7](#0-6) 

## Impact Explanation

**High — Theft of unclaimed yield.**

Every deposit made while the rsETH rate is stale mints excess rsETH/wrsETH to the depositor. The pool's collected collateral, when bridged to L1 and deposited into the LRT protocol, mints less rsETH than the outstanding wrsETH claims. The shortfall is a direct extraction of accrued yield from all existing rsETH/wrsETH holders. The magnitude scales with both the staleness duration and deposit size: at 5% APY, 30 days of staleness creates ~0.4% mispricing per deposit.

## Likelihood Explanation

**Medium.** `updateRate()` is permissionless but requires the caller to pay LayerZero messaging fees. There is no on-chain mechanism that forces a rate update before a deposit is accepted. Staleness can arise from: LayerZero delivery delays or failures, high gas costs discouraging updates, or simple operational gaps. The condition is not attacker-controlled but is a realistic operational state. Once stale, any depositor — including a deliberate attacker — can exploit it repeatedly until the rate is refreshed.

## Recommendation

Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and enforce it in `getRate()`:

```solidity
uint256 public maxStaleness; // e.g., 86400 (24 hours)

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This causes all pool deposits to revert when the rsETH rate is stale, preventing mispriced minting until the rate is refreshed via LayerZero.

## Proof of Concept

1. `RSETHRateReceiver` on an L2 holds `rate = 1.050e18`, `lastUpdated` = 30 days ago. No staleness check prevents its use.
2. Actual L1 rsETH/ETH rate is `1.054e18` (yield has accrued).
3. Attacker calls `RSETHPoolV3.deposit(wstETH, 100e18, "")`.
4. Pool fetches:
   - `rsETHToETHrate = 1.050e18` (stale, from `CrossChainRateReceiver.getRate()`)
   - `tokenToETHRate = 1.15e18` (live wstETH/ETH from `ChainlinkOracleForRSETHPoolCollateral.getRate()`)
5. Pool computes: `rsETHAmount = 100e18 * 1.15e18 / 1.050e18 ≈ 109.52 wrsETH`
6. Fair value at current rate: `100e18 * 1.15e18 / 1.054e18 ≈ 109.11 wrsETH`
7. Attacker receives **~0.41 wrsETH excess** per 100 wstETH deposited.
8. Attacker bridges wrsETH to L1, unwraps, and redeems at the true rate — extracting yield from existing holders.

**Foundry fork test plan**: Fork an L2 where `RSETHPoolV3` is deployed. Warp `block.timestamp` forward by 30 days without calling `updateRate()`. Call `deposit(wstETH, 100e18, "")`. Assert that `rsETHAmount` received exceeds `100e18 * chainlinkWstETHRate / trueRsETHRate` by the expected margin.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-105)
```text
        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-137)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        uint256 rateReceiversLength = rateReceivers.length;

        for (uint256 i; i < rateReceiversLength;) {
            uint16 dstChainId = uint16(rateReceivers[i]._chainId);

            bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );

            unchecked {
                ++i;
            }
        }

        emit RateUpdated(rate);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L119-124)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3.sol (L328-334)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L340-346)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L305-311)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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
