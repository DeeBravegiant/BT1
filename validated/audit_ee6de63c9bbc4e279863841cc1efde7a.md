Audit Report

## Title
Missing Bounds Validation on `setPricePercentageLimit` Disables rsETH Price Deviation Protection by Default — (File: `contracts/LRTOracle.sol`)

## Summary
`LRTOracle.setPricePercentageLimit` writes the caller-supplied value directly to storage with no lower-bound check, and `initialize` never sets a non-zero default. Because Solidity zero-initialises storage, `pricePercentageLimit` is `0` from the moment the contract is deployed, and both the upside access-control guard and the downside auto-pause guard explicitly short-circuit to `false` when the value is zero. The contract therefore fails to deliver its promised price-deviation protection from block 0 of deployment until an admin explicitly sets a non-zero value.

## Finding Description
`initialize` does not set `pricePercentageLimit`:

```solidity
// contracts/LRTOracle.sol L64-68
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
    emit UpdatedLRTConfig(lrtConfigAddr);
}
```

`setPricePercentageLimit` accepts any value, including zero, with no validation:

```solidity
// contracts/LRTOracle.sol L125-128
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

Both guards inside `_updateRsETHPrice` gate on `pricePercentageLimit > 0`:

```solidity
// L256-257  (upside guard)
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

// L273-274  (downside guard)
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

When `pricePercentageLimit == 0` (the default), both boolean expressions evaluate to `false` unconditionally, regardless of the magnitude of the price move. The public entry point `updateRSETHPrice()` (L87-89) is callable by any address, so any caller can trigger a price update while the guards are inactive.

Exploit path:
1. `LRTOracle` is deployed; `pricePercentageLimit` is `0` (no admin action required).
2. Rewards accrue; `_getTotalEthInProtocol()` returns a value significantly above the previous TVL.
3. An unprivileged EOA calls `updateRSETHPrice()`. `isPriceIncreaseOffLimit` is `false`; the call succeeds and `rsETHPrice` is updated without manager authorisation, bypassing the intended access-control gate.
4. Separately, a slashing event reduces TVL sharply. An unprivileged EOA calls `updateRSETHPrice()`. `isPriceDecreaseOffLimit` is `false`; the deposit pool and withdrawal manager are **not** paused, and users continue to transact at the slashed price — the auto-pause that the protocol promises never fires.
5. An admin later calls `setPricePercentageLimit(0)` (no revert); the same unprotected state is silently restored.

## Impact Explanation
The contract fails to deliver its promised price-deviation protection. The upside guard — which is designed to require manager authorisation for large price increases — is silently bypassed, allowing any caller to commit arbitrarily large (TVL-backed) price jumps. The downside guard — which is designed to auto-pause the deposit pool and withdrawal manager on sharp price drops — never fires, leaving users able to transact at a slashed price after a slashing event. This matches the allowed impact: **Low — contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
The unprotected state is the **default** and requires zero attacker action to reach; it exists from the first block of deployment. Any delay by the admin in calling `setPricePercentageLimit` with a non-zero value leaves the window open. The setter itself has no lower bound, so the zero state can be re-introduced at any time without the contract objecting. `updateRSETHPrice()` is an unrestricted public function callable by any EOA or contract.

## Recommendation
1. Add a non-zero lower bound (and optionally an upper bound) in `setPricePercentageLimit`:

```solidity
uint256 public constant MIN_PRICE_PERCENTAGE_LIMIT = 0.001e18; // 0.1%
uint256 public constant MAX_PRICE_PERCENTAGE_LIMIT = 0.5e18;   // 50%

function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    if (_pricePercentageLimit < MIN_PRICE_PERCENTAGE_LIMIT ||
        _pricePercentageLimit > MAX_PRICE_PERCENTAGE_LIMIT)
        revert InvalidPricePercentageLimit();
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

2. Set `pricePercentageLimit` to a safe default inside `initialize` so the guards are active from the first price update.

## Proof of Concept
Foundry unit test outline:

```solidity
function test_priceGuardsBypassedWhenLimitIsZero() public {
    // pricePercentageLimit is 0 by default — no setup needed

    // Simulate a 10% TVL increase (e.g., via mock oracle returning higher value)
    mockOracle.setPrice(address(asset), initialPrice * 110 / 100);

    // Unprivileged caller (not MANAGER) can commit the large price increase
    vm.prank(unprivilegedUser);
    lrtOracle.updateRSETHPrice(); // must NOT revert

    // Confirm rsETHPrice was updated (upside guard bypassed)
    assertGt(lrtOracle.rsETHPrice(), previousPrice);

    // Simulate a 10% TVL decrease (e.g., slashing)
    mockOracle.setPrice(address(asset), initialPrice * 90 / 100);

    vm.prank(unprivilegedUser);
    lrtOracle.updateRSETHPrice(); // must NOT revert

    // Confirm deposit pool was NOT paused (downside guard bypassed)
    assertFalse(lrtDepositPool.paused());
}
```