Audit Report

## Title
Stale agETH Rate in `CrossChainRateReceiver` Allows Over-Minting of agETH via `AGETHPoolV3.deposit` — (`contracts/agETH/AGETHPoolV3.sol`)

## Summary

`CrossChainRateReceiver.getRate()` returns the last stored rate with no staleness check, despite storing a `lastUpdated` timestamp. `AGETHPoolV3` uses this rate as the denominator in its mint formula for both ETH and token deposits. When the stored rate is stale-low relative to the true agETH/ETH rate, every depositor receives more agETH than their deposit backs, causing unbounded protocol insolvency proportional to the rate divergence and deposit volume.

## Finding Description

**Root cause — `getRate()` ignores `lastUpdated`:**

`CrossChainRateReceiver` stores both `rate` and `lastUpdated` on every `lzReceive` call, but `getRate()` returns `rate` unconditionally:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L13-16
uint256 public rate;
uint256 public lastUpdated;  // stored but never read in getRate()

// L102-105
function getRate() external view returns (uint256) {
    return rate;  // no block.timestamp - lastUpdated check
}
``` [1](#0-0) [2](#0-1) 

**Rate update is permissionless but requires fee payment:**

`MultiChainRateProvider.updateRate()` is `external payable nonReentrant` with no role restriction. The caller must supply ETH for LayerZero fees. There is no on-chain keeper or automation enforcing periodic updates. [3](#0-2) 

**`AGETHPoolV3` uses the stale rate directly in the mint formula:**

Both deposit paths call `viewSwapAgETHAmountAndFee`, which divides by the stale `agETHToETHrate`:

```solidity
// contracts/agETH/AGETHPoolV3.sol L187-194
uint256 agETHToETHrate = getRate();                                    // stale-low
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // current
agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;        // inflated
``` [4](#0-3) 

For ETH deposits:
```solidity
// contracts/agETH/AGETHPoolV3.sol L164-168
uint256 agETHToETHrate = getRate();
agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;  // also inflated
``` [5](#0-4) 

**Mint executes with the inflated amount — no further validation:** [6](#0-5) 

`getRate()` on the pool delegates directly to `IOracle(agETHOracle).getRate()`, which is the `AGETHRateReceiver` (`CrossChainRateReceiver`): [7](#0-6) 

No guard exists between the rate computation and the mint call.

## Impact Explanation

**Critical — Protocol insolvency.**

If the true agETH/ETH rate is `1.05e18` but the receiver holds a stale `1.00e18`, a depositor of 1 wstETH (oracle rate `1.05e18`) receives:

```
agETHAmount = 1e18 * 1.05e18 / 1.00e18 = 1.05e18 agETH
```

instead of the correct `1.00e18 agETH`. The 5% excess is unbacked. Repeated deposits during the staleness window drain the protocol's backing proportional to `(trueRate - staleRate) / trueRate × depositVolume`. This is a direct, concrete path to protocol insolvency — an allowed Critical impact.

## Likelihood Explanation

- agETH accrues yield continuously, so any gap between `updateRate()` calls creates a stale-low condition.
- `updateRate()` requires the caller to pay LayerZero fees; no on-chain keeper is enforced.
- LayerZero message delivery is not instantaneous; network congestion or fee shortfalls can delay updates for hours.
- The attack requires zero privileged access: any user can call `deposit(token, amount, referralId)` or `deposit(referralId)` on `AGETHPoolV3`.
- The condition is passively exploitable — even non-malicious depositors extract unbacked agETH during any staleness window.

## Recommendation

1. **Add a staleness threshold in `CrossChainRateReceiver.getRate()`:**
   ```solidity
   uint256 public constant MAX_RATE_AGE = 24 hours;
   function getRate() external view returns (uint256) {
       require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate stale");
       return rate;
   }
   ```
2. **Mirror the check in `AGETHPoolV3`**: before using `agETHToETHrate` in `viewSwapAgETHAmountAndFee`, verify `lastUpdated` is within an acceptable window.
3. **Add a circuit-breaker** that pauses deposits when the rate has not been refreshed within the threshold.

## Proof of Concept

```solidity
// Fork test (L2 fork, e.g. Arbitrum)
// 1. Fork with AGETHPoolV3 using AGETHRateReceiver as agETHOracle
// 2. Simulate staleness: vm.store rate slot to 1.00e18, warp +48 hours
//    vm.store(address(agETHRateReceiver), bytes32(uint256(0)), bytes32(1e18));
//    vm.warp(block.timestamp + 48 hours);
// 3. Ensure wstETH oracle returns 1.05e18 (current)
// 4. Attacker deposits 1e18 wstETH:
//    pool.deposit(wstETH, 1e18, "");
// 5. Assert attacker received 1.05e18 agETH instead of 1.00e18:
//    assertEq(agETH.balanceOf(attacker), 1.05e18);
// 6. Repeat to drain backing proportional to rate divergence
```

The `rate` storage slot is public and readable; `vm.store` can directly set it to a stale-low value without any privileged access, making this fully reproducible in a Foundry fork test.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-113)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L103-106)
```text
    /// @dev Gets the rate from the agETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(agETHOracle).getRate();
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L151-151)
```text
        agETH.mint(msg.sender, agETHAmount);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L164-168)
```text
        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L187-194)
```text
        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```
