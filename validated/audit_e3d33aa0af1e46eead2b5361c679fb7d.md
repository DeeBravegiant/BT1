Audit Report

## Title
Stale Cross-Chain Rate in `CrossChainRateReceiver.getRate()` Enables Yield Dilution of Existing rsETH Holders - (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the stored `rate` with no staleness check against `lastUpdated`. Both `RSETHPoolV3` and `RSETHPoolNoWrapper` use this rate to compute how much rsETH to mint per deposited ETH. When the L2 rate lags the true L1 rsETH price — which rises continuously as staking rewards accrue — any depositor can receive more rsETH than the current L1 rate entitles them to, diluting the accrued yield of existing rsETH holders.

## Finding Description
`CrossChainRateReceiver` stores the rsETH/ETH rate in `rate` and records the update time in `lastUpdated`. The rate is updated only when a LayerZero message arrives via `lzReceive()`: [1](#0-0) 

`getRate()` returns the stored value unconditionally, with no check on `lastUpdated`: [2](#0-1) 

Both pools delegate to this oracle via `IOracle(rsETHOracle).getRate()` and compute the mint amount as:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate
``` [3](#0-2) [4](#0-3) 

If `rsETHToETHrate` is stale (lower than the true L1 price), `rsETHAmount` is inflated. The depositor receives more rsETH than the current L1 rate entitles them to.

The rate is propagated from L1 by an off-chain keeper. Between keeper calls — which can span hours or days — the L2 rate drifts below the true L1 price as staking rewards accrue. This window is publicly observable on-chain by comparing `lastUpdated` against `block.timestamp`.

`RSETHPoolNoWrapper` is more exposed because it has no `dailyMintLimit` guard, allowing unbounded extraction in a single block: [5](#0-4) 

`RSETHPoolV3` has a `limitDailyMint` modifier that caps per-day rsETH minting, partially mitigating the exposure: [6](#0-5) 

## Impact Explanation
When new rsETH is minted at a stale (lower) rate, the new depositor acquires a larger fractional share of the protocol's total underlying assets than they paid for. The total underlying assets do not change; only the rsETH supply increases beyond what the true rate would justify. This directly reduces the per-token ETH value that existing rsETH holders can claim — i.e., it transfers accrued staking yield from existing holders to the new depositor.

**Impact: High — Theft of unclaimed yield.**

## Likelihood Explanation
The rate update is not automatic; it requires an off-chain keeper to call `updateRate()` on `RSETHMultiChainRateProvider`, which then sends a LayerZero message. Keeper latency of several hours is routine. The staleness window is publicly observable via `lastUpdated`. Any depositor can monitor L1 `LRTOracle.rsETHPrice` against the L2 `CrossChainRateReceiver.rate` and deposit precisely when the gap is widest. No privileged access, no oracle compromise, and no front-running of other users is required — only timing a public deposit call.

**Likelihood: Medium.**

## Recommendation
Add a configurable maximum staleness threshold to `CrossChainRateReceiver.getRate()`. If `block.timestamp - lastUpdated` exceeds the threshold, revert rather than return a stale rate:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This causes L2 pool deposits to revert when the oracle is stale, preventing exploitation of the lag window. The keeper is then incentivised to update the rate promptly to restore deposit functionality.

## Proof of Concept
1. L1 `LRTOracle.rsETHPrice` = **1.050 ETH** (after 48 hours of staking rewards).
2. L2 `CrossChainRateReceiver.rate` = **1.030 ETH** (last updated 48 hours ago; keeper has not sent a new message).
3. Attacker calls `RSETHPoolNoWrapper.deposit{value: 100 ETH}(referralId)`.
4. Pool computes: `rsETHAmount = 100e18 * 1e18 / 1.030e18 ≈ 97.087 rsETH`.
5. At the true L1 rate the attacker should have received: `100e18 / 1.050e18 ≈ 95.238 rsETH`.
6. Attacker receives **1.849 rsETH excess** — yield stolen from existing holders.
7. Keeper sends updated rate (1.050 ETH) via LayerZero. Attacker's 97.087 rsETH is now redeemable for `97.087 × 1.050 ≈ 101.94 ETH`, a **1.94 ETH profit** on a 100 ETH deposit, funded entirely by dilution of existing holders' accrued staking rewards.

**Foundry fork test plan:**
- Fork an L2 where `RSETHPoolNoWrapper` is deployed.
- Warp `block.timestamp` forward by 48 hours without triggering `lzReceive()` (simulating keeper inactivity).
- Call `deposit{value: 100 ether}` as an unprivileged attacker.
- Assert `rsETHAmount > 100e18 * 1e18 / trueL1Rate` to confirm inflation.
- Simulate keeper calling `lzReceive()` with the updated rate.
- Assert attacker's rsETH balance × new rate > 100 ETH (profit confirmed).

### Citations

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
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
