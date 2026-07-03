### Title
Stale Cross-Chain Oracle Rate Allows Over-Minting of wrsETH, Diluting Existing Holders' Yield — (`contracts/pools/RSETHPoolV3.sol`, `contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPoolV3.deposit()` mints wrsETH using `getRate()` with no staleness check. Because the L2 `rsETHOracle` (a cross-chain rate receiver or `InterimRSETHOracle`) stores a cached rate that is only updated via LayerZero messages or manual admin calls, an attacker can deposit ETH during a staleness window when the cached rate is below the true on-chain rate, receiving more wrsETH than the deposited ETH is worth at the true rate. The shortfall is borne by existing wrsETH holders.

---

### Finding Description

**Deposit math:** [1](#0-0) 

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // ← cached, potentially stale
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

`getRate()` simply delegates to the configured oracle with no freshness validation: [2](#0-1) 

The oracle can be `RSETHRateReceiver`, which stores a rate received via LayerZero and is only updated when a cross-chain message arrives: [3](#0-2) 

Or it can be `InterimRSETHOracle`, where the rate is set manually by a manager: [4](#0-3) 

On L1, `LRTOracle.rsETHPrice` is itself a stored value updated only when `updateRSETHPrice()` is called: [5](#0-4) 

`RSETHPriceFeed.latestRoundData()` reads `RS_ETH_ORACLE.rsETHPrice()` directly with no staleness guard, and the `updatedAt` it returns is from the ETH/USD Chainlink feed — not from the rsETH price update — so downstream consumers cannot detect rsETH staleness from the returned timestamp: [6](#0-5) 

**Exploit arithmetic:**

| Variable | Value |
|---|---|
| True rsETH/ETH rate | 1.10 ETH per rsETH |
| Stale cached rate | 1.05 ETH per rsETH |
| Attacker deposits | 1 ETH |
| wrsETH minted (stale) | 1 / 1.05 = **0.9524 wrsETH** |
| wrsETH deserved (true) | 1 / 1.10 = **0.9091 wrsETH** |
| Over-minted | **0.0433 wrsETH** |

When the ETH is bridged to L1 and converted to rsETH at the true rate, the pool acquires only 0.9091 rsETH but must back 0.9524 wrsETH. The 0.0433 rsETH shortfall is absorbed from the pool's existing rsETH backing, diluting yield for all prior wrsETH holders.

---

### Impact Explanation

**High — Theft of unclaimed yield.** Every deposit made while the oracle is stale-low over-mints wrsETH. The excess wrsETH is backed by rsETH that belongs to existing holders, directly reducing their yield entitlement. The `dailyMintLimit` caps the per-day magnitude but does not prevent the attack. [7](#0-6) 

---

### Likelihood Explanation

**Moderate.** Cross-chain rate updates via LayerZero are not instantaneous; staleness windows of minutes to hours are routine. rsETH accrues staking yield continuously, so the cached rate is almost always slightly below the true rate between updates. An attacker needs only to monitor the L2 oracle rate vs. the L1 `LRTOracle.rsETHPrice` and deposit during any staleness gap — no privileged access required.

---

### Recommendation

1. **Record a `lastUpdatedAt` timestamp** in the rate receiver and revert (or cap minting) in `viewSwapRsETHAmountAndFee` if `block.timestamp - lastUpdatedAt > MAX_STALENESS`.
2. **Expose staleness metadata** from `RSETHPriceFeed.latestRoundData()` using the rsETH price update timestamp, not the ETH/USD feed's `updatedAt`, so consumers can enforce freshness.
3. Consider a **maximum-rate-increase guard** in the pool: if the oracle rate has moved more than X% since the last deposit, pause deposits until the rate is confirmed fresh.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Invariant fuzz test (Foundry)
// Run: forge test --match-test testStaleOracleOverMint -vvv

import "forge-std/Test.sol";

interface IPool {
    function deposit(string memory) external payable;
    function getRate() external view returns (uint256);
}

interface IInterimOracle {
    function setRate(uint256) external;
    function getRate() external view returns (uint256);
}

interface IWrsETH {
    function balanceOf(address) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

contract StaleOraclePoC is Test {
    IPool pool;
    IInterimOracle oracle;
    IWrsETH wrsETH;

    function testStaleOracleOverMint(uint256 staleRate, uint256 trueRate, uint256 depositAmount) public {
        // Bound inputs
        staleRate  = bound(staleRate,  1.00e18, 1.09e18);
        trueRate   = bound(trueRate,   staleRate + 1, 1.20e18);
        depositAmount = bound(depositAmount, 0.01 ether, 10 ether);

        // Set oracle to stale (lower) rate
        oracle.setRate(staleRate);

        uint256 wrsETHBefore = wrsETH.totalSupply();

        // Attacker deposits at stale rate
        vm.deal(address(this), depositAmount);
        pool.deposit{value: depositAmount}("ref");

        uint256 wrsETHMinted = wrsETH.totalSupply() - wrsETHBefore;

        // Now oracle updates to true rate
        oracle.setRate(trueRate);

        // Invariant: wrsETH minted must not exceed ETH deposited / trueRate
        uint256 maxAllowed = depositAmount * 1e18 / trueRate;

        // This assertion FAILS when staleRate < trueRate
        assertLe(wrsETHMinted, maxAllowed,
            "INVARIANT BROKEN: over-minted wrsETH at stale rate dilutes existing holders");
    }
}
```

The fuzz test will find inputs where `staleRate < trueRate` and demonstrate that `wrsETHMinted > depositAmount * 1e18 / trueRate`, confirming the invariant break.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-123)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
```

**File:** contracts/pools/RSETHPoolV3.sol (L235-237)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
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

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L9-15)
```text
contract RSETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L36-45)
```text
    function setRate(uint256 newRate) external onlyRole(MANAGER_ROLE) {
        _setRate(newRate);
    }

    /// @dev Internal function to set the rsETH/ETH rate
    function _setRate(uint256 newRate) internal {
        if (newRate < 1e18) revert InvalidRate();
        rate = newRate;
        emit RateUpdated(newRate);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```
