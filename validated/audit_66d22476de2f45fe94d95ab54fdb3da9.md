Audit Report

## Title
Stale Manually-Set Rate in `InterimRSETHOracle` Enables Over-Minting of rsETH on L2 Pools - (File: contracts/pools/oracle/InterimRSETHOracle.sol)

## Summary
`InterimRSETHOracle` stores a single `uint256 public rate` set by a privileged `MANAGER_ROLE` with no last-updated timestamp and no staleness guard in `getRate()`. Every L2 pool contract reads this rate unconditionally to compute how many rsETH tokens to mint per deposited ETH. When the true rsETH/ETH exchange rate rises due to staking reward accrual but the oracle is not updated, any depositor receives more rsETH than their ETH contribution warrants, diluting the yield accrued by existing rsETH holders.

## Finding Description
`InterimRSETHOracle` declares a bare storage variable with no timestamp: [1](#0-0) 

`_setRate()` only validates `newRate >= 1e18` and records no update time: [2](#0-1) 

`getRate()` returns the stored value unconditionally with no freshness check: [3](#0-2) 

Every pool's deposit path calls `viewSwapRsETHAmountAndFee()`, which divides the deposited amount by this rate. In `RSETHPoolV2`: [4](#0-3) 

The same pattern is present in `RSETHPoolV3`: [5](#0-4) 

In `RSETHPoolV2NBA`: [6](#0-5) 

And in `RSETHPoolV3WithNativeChainBridge`: [7](#0-6) 

Because `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`, a stale (too-low) rate produces a larger `rsETHAmount` than the depositor deserves. The daily mint limit in `RSETHPoolV2` and `RSETHPoolV3` does not prevent the over-minting — it only caps total daily rsETH issuance, but every unit minted within that cap is still over-issued at the stale rate. [8](#0-7) 

## Impact Explanation
**High — Theft of unclaimed yield.**

rsETH is a yield-bearing token: its ETH value rises continuously as staking rewards accrue. Existing holders' rsETH represents a proportional claim on the underlying ETH pool. When new depositors receive more rsETH than their ETH contribution warrants (because the oracle rate is stale and too low), the total rsETH supply is inflated beyond what the underlying ETH supports. This directly reduces the ETH-per-rsETH ratio for all existing holders, transferring the yield they have already accrued to the new depositors. This is a concrete, quantifiable theft of unclaimed yield matching the allowed High impact class.

## Likelihood Explanation
**Medium.**

The contract is explicitly described as an interim solution pending a more robust oracle, making manual update dependency a known operational risk. rsETH accrues staking rewards continuously, so any gap in updates creates a discrepancy. No special knowledge, front-running, or privileged access is required — any depositor calling the public `deposit()` function during a staleness window passively exploits the condition. Network congestion, key unavailability, or routine operational oversight are realistic causes of staleness.

## Recommendation
1. Add a `uint256 public lastUpdated` field to `InterimRSETHOracle` and record `lastUpdated = block.timestamp` inside `_setRate()`.
2. Add a staleness guard in `getRate()` that reverts if `block.timestamp - lastUpdated > MAX_STALENESS` (e.g., 24 hours).
3. Long-term, replace `InterimRSETHOracle` with a live on-chain oracle (e.g., a Chainlink feed or a cross-chain message from `LRTOracle`) so the rate tracks the true rsETH/ETH exchange rate without manual intervention.

## Proof of Concept
1. Deploy `InterimRSETHOracle` with `rate = 1.05e18` and configure it as `rsETHOracle` in `RSETHPoolV2`.
2. rsETH accrues staking rewards; the true rate rises to `1.10e18`. The `MANAGER_ROLE` does not call `setRate()`.
3. An unprivileged attacker calls `RSETHPoolV2.deposit{value: 1 ether}("")`.
4. Inside `viewSwapRsETHAmountAndFee(1e18)`:
   - `fee = 0` (assume `feeBps = 0` for clarity)
   - `rsETHToETHrate = getRate()` → returns stale `1.05e18`
   - `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.9524 rsETH`
5. At the correct rate `1.10e18`, the attacker should receive `1e18 * 1e18 / 1.10e18 ≈ 0.9091 rsETH`.
6. The attacker receives **~0.0433 extra rsETH** per ETH deposited, extracted from yield accrued by existing holders.
7. This is repeatable by any depositor for the entire duration of the staleness window.

**Foundry fork test plan:**
```solidity
function testStaleOracleOverMint() public {
    // Fork L2 chain with InterimRSETHOracle deployed
    // Record existing holder's rsETH balance and total supply
    uint256 supplyBefore = wrsETH.totalSupply();
    // Do NOT call setRate() — simulate staleness
    // Attacker deposits 1 ETH
    vm.prank(attacker);
    pool.deposit{value: 1 ether}("");
    // Assert attacker received more rsETH than 1e18 * 1e18 / trueRate
    uint256 attackerBalance = wrsETH.balanceOf(attacker);
    uint256 fairAmount = 1e18 * 1e18 / trueRate;
    assertGt(attackerBalance, fairAmount);
    // Assert existing holder's proportional claim decreased
}
```

### Citations

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L14-15)
```text
    /// @notice The current rsETH/ETH rate
    uint256 public rate;
```

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L41-45)
```text
    function _setRate(uint256 newRate) internal {
        if (newRate < 1e18) revert InvalidRate();
        rate = newRate;
        emit RateUpdated(newRate);
    }
```

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L49-51)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L72-93)
```text
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
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
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-233)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L335-344)
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
