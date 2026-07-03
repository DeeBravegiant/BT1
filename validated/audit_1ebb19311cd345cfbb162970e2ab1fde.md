Audit Report

## Title
Absence of Minimum Bound on `pricePercentageLimit` Silently Disables Downside Auto-Pause and Upside Gate - (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle.setPricePercentageLimit` accepts `0` with no lower-bound guard, and `initialize` never assigns `pricePercentageLimit`, so it defaults to `0` on every fresh deployment. When `pricePercentageLimit == 0`, both the upside gate and the downside circuit-breaker inside `_updateRsETHPrice` are unconditionally disabled via their `pricePercentageLimit > 0 &&` short-circuit. A slashing event that reduces total ETH in the protocol will not trigger the protective pause, allowing new depositors to buy in at the depressed rsETH price and dilute all existing rsETH holders' proportional claim on protocol assets.

## Finding Description

`initialize` (lines 64–68) never sets `pricePercentageLimit`, so it is `0` from the first block. `setPricePercentageLimit` (lines 125–128) imposes no minimum:

```solidity
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    pricePercentageLimit = _pricePercentageLimit; // no minimum check
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

Inside `_updateRsETHPrice`, both safety rails are gated on `pricePercentageLimit > 0`:

**Upside gate** (lines 256–257):
```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**Downside circuit-breaker** (lines 273–274):
```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

When `pricePercentageLimit == 0`, both booleans are `false` regardless of the magnitude of the price movement. The downside branch (lines 277–282) that pauses `lrtDepositPool`, `withdrawalManager`, and the oracle itself is never reached. `updateRSETHPrice()` is a public function (line 87), callable by any address.

**Exploit flow:**
1. Contract is deployed; `pricePercentageLimit` is `0` (default, no admin action required).
2. An EigenLayer slashing event reduces total ETH in the protocol by, e.g., 10%.
3. Any address calls `updateRSETHPrice()`.
4. `_updateRsETHPrice` computes `newRsETHPrice` ~10% below `highestRsethPrice`.
5. `isPriceDecreaseOffLimit = (0 > 0) && … = false`; the auto-pause branch is skipped.
6. `rsETHPrice` is committed at the depressed value; `lrtDepositPool` and `withdrawalManager` remain unpaused.
7. An attacker deposits ETH at the depressed price, receiving more rsETH per ETH than the pre-slashing backing ratio.
8. When the protocol recovers (e.g., validator rewards rebuild TVL), the attacker's inflated rsETH share captures yield that would otherwise have accrued to pre-existing holders — permanently diluting their proportional claim.

Existing guards are insufficient: the `pricePercentageLimit > 0` short-circuit is the only guard, and it is trivially bypassed by the default zero value.

## Impact Explanation

**High — Theft of unclaimed yield.**

Pre-existing rsETH holders hold a proportional claim on future protocol yield (staking rewards, validator income). When a new depositor buys in at a slashing-depressed price without the protective pause triggering, the rsETH supply is inflated relative to the backing at that moment. Upon recovery, the attacker's disproportionate rsETH share captures a fraction of the yield that belonged to prior holders. This is a direct, permanent transfer of accrued yield from existing holders to the attacker, matching the "High — Theft of unclaimed yield" impact class.

## Likelihood Explanation

The default state (`pricePercentageLimit == 0`) requires zero admin action to reach — it is the state of every freshly deployed contract. EigenLayer slashing is a documented, non-hypothetical risk for LST protocols. `updateRSETHPrice()` is permissionlessly callable. The attacker needs only to monitor for a TVL decrease, call the public price-update function, and deposit. No privileged access, no oracle manipulation, and no social engineering are required. The attack is repeatable across any deployment window where `pricePercentageLimit` has not been explicitly set.

## Recommendation

1. Introduce a non-zero minimum constant and enforce it in the setter:
```solidity
uint256 public constant MIN_PRICE_PERCENTAGE_LIMIT = 1e15; // 0.1%

function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    if (_pricePercentageLimit < MIN_PRICE_PERCENTAGE_LIMIT) revert PricePercentageLimitTooLow();
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```
2. Assign a safe default in `initialize` so protection is active from the first block:
```solidity
pricePercentageLimit = 5e16; // 5%
```

## Proof of Concept

**Foundry fork test outline:**

```solidity
function test_slashingDilutesHoldersWhenLimitIsZero() public {
    // 1. Deploy with pricePercentageLimit == 0 (default, no setPricePercentageLimit call)
    // 2. Mint rsETH to alice (existing holder) via depositETH
    // 3. Simulate 10% slashing: reduce totalAssetDeposits by 10% via mock
    // 4. Call lrtOracle.updateRSETHPrice() as unprivileged address (bob)
    // 5. Assert lrtDepositPool.paused() == false  (auto-pause did NOT trigger)
    // 6. Bob calls depositETH{value: 1 ether}
    // 7. Simulate protocol recovery: restore totalAssetDeposits to original value
    // 8. Call updateRSETHPrice() again
    // 9. Assert alice's proportional ETH backing (rsETHBalance * rsETHPrice) is less
    //    than it was before the slashing — yield transferred to bob
}
```

Key assertions:
- After step 4: `lrtOracle.paused() == false`, `lrtDepositPool.paused() == false`
- After step 6: `rsETH.balanceOf(bob) > 1 ether / highestRsethPrice` (bob got more rsETH than pre-slashing price would give)
- After step 9: `alice_backing_after < alice_backing_before` — permanent dilution confirmed