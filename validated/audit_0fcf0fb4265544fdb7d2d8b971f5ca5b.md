Audit Report

## Title
Stale Rate in `CrossChainRateReceiver.getRate()` Enables Yield Dilution via `AGETHPoolV3.deposit` — (`contracts/cross-chain/CrossChainRateReceiver.sol`, `contracts/agETH/AGETHPoolV3.sol`)

## Summary

`CrossChainRateReceiver.getRate()` stores a `lastUpdated` timestamp but never validates it, returning the last received `rate` unconditionally. `AGETHPoolV3.deposit` uses this rate to compute and mint agETH to depositors. When the stored rate is stale (lower than the true current rate because agETH has accrued yield since the last LayerZero push), any depositor receives more agETH than their ETH entitles them to at the true rate, diluting the yield pool of existing agETH holders.

## Finding Description

**Root cause — no staleness guard in `getRate()`:**

`CrossChainRateReceiver` tracks `lastUpdated` but the value is written only in `lzReceive` and is never read in `getRate()`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L16, L103-105
uint256 public lastUpdated;   // set in lzReceive, never read in getRate()

function getRate() external view returns (uint256) {
    return rate;              // no staleness check
}
```

`rate` is updated exclusively via `lzReceive`, which requires a LayerZero message from the authorised source chain and provider address. Any delay in message delivery (network congestion, infrequent push cadence, bridge downtime) causes `rate` to silently age below the true agETH/ETH exchange rate.

**Exploit path — `AGETHPoolV3.deposit`:**

`AGETHPoolV3.getRate()` delegates directly to `IOracle(agETHOracle).getRate()`, which resolves to `AGETHRateReceiver` (a `CrossChainRateReceiver`):

```solidity
// contracts/agETH/AGETHPoolV3.sol L104-106
function getRate() public view returns (uint256) {
    return IOracle(agETHOracle).getRate();
}
```

`viewSwapAgETHAmountAndFee` uses this rate to compute the agETH amount:

```solidity
// contracts/agETH/AGETHPoolV3.sol L160-169
function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 agETHToETHrate = getRate();           // stale → too low
    agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;  // inflated
}
```

`deposit` mints the inflated `agETHAmount` directly to the caller with no further validation:

```solidity
// contracts/agETH/AGETHPoolV3.sol L115-128
function deposit(string memory referralId) external payable nonReentrant {
    ...
    (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    agETH.mint(msg.sender, agETHAmount);
    ...
}
```

`feeBps` is admin-settable to any value including 0, maximising the theft delta when set to zero:

```solidity
// contracts/agETH/AGETHPoolV3.sol L245-251
function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    feeBps = _feeBps;
    ...
}
```

**Why existing checks are insufficient:** The only guards in `deposit` are `isEthDepositEnabled` and `amount == 0`. Neither addresses rate staleness. `nonReentrant` is irrelevant to this vector. There is no minimum rate check, no circuit breaker on rate age, and no slippage parameter.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing agETH holders have accrued yield represented by the true rate rising above the stored stale rate. A depositor minting at the stale (lower) rate receives more agETH than their ETH is worth at the true rate. This excess agETH dilutes the pool, transferring yield that belonged to existing holders to the new depositor. The theft scales linearly with deposit size and the rate gap, with no cap. At `feeBps=0` with `staleRate=1.00e18`, `trueRate=1.05e18`, and a 100 ETH deposit, the attacker captures ~4.762 agETH of yield that belonged to existing holders.

## Likelihood Explanation

The attack requires no privileged access, no governance capture, and no external protocol compromise. Any unprivileged user can:
1. Read the on-chain `rate` from `AGETHRateReceiver`.
2. Compare it against the true agETH/ETH rate on the source chain (publicly observable).
3. Call `AGETHPoolV3.deposit` with maximum ETH whenever a gap exists.

LayerZero message delivery is not instantaneous and push cadence is not guaranteed to be continuous. Any period of bridge congestion or infrequent rate updates creates an exploitable window. The attack is permissionless and repeatable.

## Recommendation

1. **Add a staleness check in `CrossChainRateReceiver.getRate()`:**
   ```solidity
   uint256 public constant MAX_RATE_AGE = 24 hours;

   function getRate() external view returns (uint256) {
       require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
       return rate;
   }
   ```
   This causes `deposit` to revert when the rate is stale, preventing minting at an outdated price.

2. **Enforce a minimum `feeBps` floor** as a secondary defence to reduce the profitability of any residual rate gap, but this does not fix the root cause.

3. **Ensure the rate-push cadence** on the provider side is frequent enough relative to expected yield accrual.

## Proof of Concept

The following differential model test is self-contained and requires no fork:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

contract StaleRatePoC {
    uint256 constant STALE_RATE  = 1.00e18;
    uint256 constant TRUE_RATE   = 1.05e18;
    uint256 constant DEPOSIT_ETH = 100e18;

    function fairAgETH(uint256 amount) internal pure returns (uint256) {
        return amount * 1e18 / TRUE_RATE;
    }

    function mintedAgETH(uint256 amount, uint256 feeBps) internal pure returns (uint256) {
        uint256 fee = amount * feeBps / 10_000;
        return (amount - fee) * 1e18 / STALE_RATE;
    }

    function testYieldTheft() external pure returns (uint256 excessAt0, uint256 excessAt100) {
        uint256 fair = fairAgETH(DEPOSIT_ETH);          // 95.238e18
        excessAt0   = mintedAgETH(DEPOSIT_ETH, 0)   - fair;  // ~4.762e18
        excessAt100 = mintedAgETH(DEPOSIT_ETH, 100) - fair;  // ~4.714e18
        // Both > 0: yield theft occurs at any feeBps value
    }
}
```

For a Foundry fork test: deploy `AGETHRateReceiver` with a fixed stale rate, deploy `AGETHPoolV3` pointing to it, call `deposit{value: 100 ether}("")`, and assert that `agETH.balanceOf(attacker) > 100 ether * 1e18 / TRUE_RATE`. The excess confirms yield theft from existing holders.