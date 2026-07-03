Audit Report

## Title
Missing Staleness Check in `CrossChainRateReceiver.getRate()` Enables Over-Distribution of rsETH from `RSETHPoolNoWrapper` — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver` records `lastUpdated` on every LayerZero message but `getRate()` returns the cached `rate` unconditionally with no age validation. `RSETHPoolNoWrapper.deposit()` uses this rate to compute how much rsETH to transfer from the pool's own finite balance. Because rsETH is an appreciating asset, a stale rate is naturally lower than the true rate, causing every depositor during a staleness window to receive more rsETH than their ETH is worth. The ETH bridged to L1 mints only the fair-value rsETH amount, leaving a permanent deficit in the pool that shortchanges or freezes future depositors.

## Finding Description

`CrossChainRateReceiver.lzReceive()` stores `lastUpdated = block.timestamp` at line 97, but `getRate()` at lines 103–105 returns `rate` with no staleness guard:

```solidity
// CrossChainRateReceiver.sol L95-105
rate = _rate;
lastUpdated = block.timestamp;   // stored, never validated in getRate()
...
function getRate() external view returns (uint256) {
    return rate;                 // unconditional
}
```

`RSETHPoolNoWrapper.getRate()` (line 220–222) delegates directly to this oracle:

```solidity
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
```

`viewSwapRsETHAmountAndFee()` (lines 277–286) divides by this unchecked rate:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

`deposit()` (lines 231–244) immediately transfers the computed amount from the pool's own rsETH balance:

```solidity
rsETH.safeTransfer(msg.sender, rsETHAmount);
```

There is no `minRsETHAmount` parameter, no slippage floor, and no circuit-breaker on `lastUpdated`. The pool is transfer-based: it holds a finite rsETH balance replenished only after the collected ETH is bridged to L1 and the L1Vault mints rsETH at the true rate. Since rsETH appreciates monotonically, any stale rate is lower than the current true rate, so `amountAfterFee * 1e18 / staleRate > amountAfterFee * 1e18 / trueRate`. The attacker extracts the difference; the subsequent L1 replenishment mints only the fair-value amount, making the deficit permanent.

Existing checks are insufficient: `nonReentrant` and `whenNotPaused` do not address oracle freshness; `isEthDepositEnabled` is a coarse kill-switch not tied to oracle age.

## Impact Explanation

**High — Theft of unclaimed yield.**

The pool's rsETH balance is pre-funded to cover future depositors including the yield component that has accrued since the last replenishment. An attacker exploiting a stale-low rate extracts this yield component (and potentially principal) from the pool. Because the L1 replenishment is proportional to the ETH deposited at the true rate, the pool's rsETH balance after replenishment is permanently lower than it should be. Remaining depositors receive less rsETH than owed, or `safeTransfer` reverts entirely once the balance is exhausted, freezing their yield.

## Likelihood Explanation

LayerZero cross-chain message delivery is subject to real-world delays (network congestion, relayer downtime, bridge pauses). Oracle staleness requires no operator compromise — it is a natural failure mode of the push-based architecture. Because rsETH appreciates continuously, any gap in updates produces a stale-low rate. The `lastUpdated` field is public, so an attacker can monitor staleness on-chain without privileged access. The attack is permissionless, requires only ETH, and is executable in a single transaction. It is repeatable for the entire duration of the staleness window.

## Recommendation

1. **Add a staleness guard in `CrossChainRateReceiver.getRate()`:**
   ```solidity
   uint256 public constant MAX_RATE_AGE = 24 hours;

   function getRate() external view returns (uint256) {
       require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Stale rate");
       return rate;
   }
   ```
2. **Add a heartbeat check in `ChainlinkOracleForRSETHPoolCollateral.getRate()`** alongside the existing `answeredInRound` check:
   ```solidity
   require(block.timestamp - timestamp <= MAX_ORACLE_AGE, "Stale price");
   ```
3. **Add a `minRsETHAmount` slippage parameter to `RSETHPoolNoWrapper.deposit()`** so depositors cannot silently receive an inflated amount and the pool cannot over-distribute.
4. **Consider a circuit-breaker** in `RSETHPoolNoWrapper` that pauses deposits when `block.timestamp - CrossChainRateReceiver(rsETHOracle).lastUpdated() > threshold`.

## Proof of Concept

```
// Foundry fork test — Unichain fork, unmodified contracts
// Setup:
//   - Fork Unichain at a block where CrossChainRateReceiver.lastUpdated is recent
//   - Record current rate R0 (e.g. 1.05e18)
// Attack:
//   1. vm.warp(block.timestamp + 25 hours)
//      → CrossChainRateReceiver.rate is now stale at R0
//      → True rate has increased to R1 > R0 (rsETH appreciates ~4% APY)
//   2. attacker.deposit{value: 100 ether}("")
//      → rsETHAmount = 100e18 * 1e18 / R0  (inflated)
//      → fair amount  = 100e18 * 1e18 / R1  (lower)
//   3. Assert attacker rsETH balance > fair amount
//   4. Assert pool rsETH balance < amount needed for next honest depositor at fair rate
//   5. Honest depositor calls deposit{value: 1 ether}("") → safeTransfer reverts (ERC20: insufficient balance)
//   6. Bridge ETH to L1, L1Vault mints only fair-value rsETH → pool deficit confirmed permanent
```

The `lastUpdated` field is publicly readable on `CrossChainRateReceiver` (line 16), making staleness trivially detectable by any external observer without privileged access. [1](#0-0) [2](#0-1) [3](#0-2)

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
