### Title
Stale Cross-Chain rsETH/ETH Rate Used in L2 Pool Deposits Without Staleness Check — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver` stores the rsETH/ETH exchange rate received via LayerZero and exposes it through `getRate()`. Every L2 pool contract (`RSETHPoolV2NBA`, `RSETHPoolV2`, `RSETHPoolV3`, `RSETHPool`, etc.) calls `IOracle(rsETHOracle).getRate()` to compute how many wrsETH tokens to mint per ETH deposited. Neither `CrossChainRateReceiver.getRate()` nor any pool contract checks `lastUpdated` before using the rate. If the off-chain bot that calls `CrossChainRateProvider.updateRate()` fails, or if LayerZero message delivery is delayed, the stored rate becomes stale. A depositor can exploit the stale (lower) rate to receive more wrsETH than the current backing justifies, at the expense of existing wrsETH holders.

---

### Finding Description

`CrossChainRateReceiver` records two state variables on every rate update:

```solidity
rate = _rate;
lastUpdated = block.timestamp;
``` [1](#0-0) 

However, `getRate()` returns `rate` unconditionally, with no reference to `lastUpdated`:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
``` [2](#0-1) 

Every L2 pool contract delegates its pricing entirely to this call. For example, `RSETHPoolV2NBA.getRate()`:

```solidity
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
``` [3](#0-2) 

The returned rate is used directly to compute the wrsETH mint amount:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [4](#0-3) 

The same pattern is present in `RSETHPoolV2`, `RSETHPoolV3`, `RSETHPool`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`. [5](#0-4) 

The rate is pushed from Ethereum mainnet by an off-chain bot calling `CrossChainRateProvider.updateRate()`, which sends a LayerZero message:

```solidity
function updateRate() external payable nonReentrant {
    uint256 latestRate = getLatestRate();
    rate = latestRate;
    lastUpdated = block.timestamp;
    ...
    ILayerZeroEndpoint(layerZeroEndpoint).send{...}(...);
}
``` [6](#0-5) 

`RSETHRateReceiver` and `RSETHMultiChainRateProvider` are the concrete implementations used in production across Arbitrum, Optimism, Polygon zkEVM, Blast, Mode, and Scroll. [7](#0-6) 

---

### Impact Explanation

rsETH accrues staking yield continuously, so its ETH value (`rsETHPrice`) increases over time. The L2 pool rate (`CrossChainRateReceiver.rate`) only reflects this increase when the off-chain bot successfully delivers a LayerZero message. If the rate is stale and lower than the true current value, the formula `rsETHAmount = amountAfterFee * 1e18 / staleRate` mints more wrsETH per ETH than the actual backing justifies. This over-minting dilutes the wrsETH/ETH backing ratio for all existing holders, constituting theft of accrued yield from them. The attacker retains the excess wrsETH permanently once the rate is updated.

**Impact: High — Theft of unclaimed yield from existing wrsETH holders.**

---

### Likelihood Explanation

The rate update path has two off-chain dependencies: (1) a bot must call `updateRate()` on the mainnet provider, and (2) LayerZero must deliver the message to the L2 receiver. Either can fail due to bot bugs, gas exhaustion, network congestion, or LayerZero infrastructure issues. rsETH yield accrues every Ethereum epoch (~6.4 minutes), so even a few hours of staleness creates a measurable exploitable gap. An attacker can monitor `CrossChainRateReceiver.lastUpdated` on-chain and act whenever the gap between the stale rate and the live mainnet `LRTOracle.rsETHPrice` is profitable.

---

### Recommendation

Add a configurable `maxStaleness` threshold to `CrossChainRateReceiver` and revert in `getRate()` if `block.timestamp - lastUpdated > maxStaleness`:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    if (block.timestamp - lastUpdated > maxStaleness) revert StaleRate();
    return rate;
}
```

This mirrors the staleness guard already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which checks `answeredInRound < roundID`. [8](#0-7) 

---

### Proof of Concept

1. The current mainnet `LRTOracle.rsETHPrice` is `1.05e18` (updated regularly on L1).
2. The off-chain bot fails to call `CrossChainRateProvider.updateRate()` for 12 hours. `CrossChainRateReceiver.rate` on Arbitrum remains at `1.03e18` (stale).
3. Attacker calls `RSETHPoolV2NBA.deposit{value: 100 ether}("")`.
4. `viewSwapRsETHAmountAndFee(100e18)` computes: `rsETHAmount = 100e18 * 1e18 / 1.03e18 ≈ 97.09 wrsETH`.
5. Correct amount at live rate: `100e18 * 1e18 / 1.05e18 ≈ 95.24 wrsETH`.
6. Attacker receives `≈1.85 excess wrsETH` (≈1.9% over-mint) at the expense of existing holders.
7. When the bot resumes and the rate updates to `1.05e18`, the attacker's wrsETH is fully backed at the correct rate, having extracted yield from prior depositors. [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L99-102)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L106-118)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L124-133)
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

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
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

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L85-101)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceiver, address(this));

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );

        emit RateUpdated(rate);
    }
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L1-16)
```text
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import { CrossChainRateReceiver } from "contracts/cross-chain/CrossChainRateReceiver.sol";

/// @title rsETH cross chain rate receiver
/// @notice Receives the rsETH rate from a provider contract on a different chain than the one this contract is deployed
/// on
contract RSETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
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
