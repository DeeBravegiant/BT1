### Title
`highestRsethPrice = 0` Bootstrap Race Causes Permanent `PriceAboveDailyThreshold` Revert for Public Callers and Division-by-Zero on Deposits — (`contracts/LRTOracle.sol`)

---

### Summary

When `LRTOracle` is deployed (or redeployed as a new proxy during migration) while rsETH already has a non-zero `totalSupply`, both `rsETHPrice` and `highestRsethPrice` are `0` (Solidity default). The initialization guard at line 224 sets `highestRsethPrice = rsETHPrice = 0` (a no-op), and the subsequent threshold check at line 257 reduces to `priceDifference > 0`, which is always true for any positive `newRsETHPrice`. Every public call to `updateRSETHPrice()` reverts with `PriceAboveDailyThreshold`, and `rsETHPrice` stays `0`, causing `LRTDepositPool.getRsETHAmountToMint()` to revert with division-by-zero on every deposit.

---

### Finding Description

**Root cause — `contracts/LRTOracle.sol`, `_updateRsETHPrice()`:**

```
Line 224:  if (highestRsethPrice == 0) {
Line 225:      highestRsethPrice = rsETHPrice;   // 0 = 0, no-op
Line 226:  }
...
Line 252:  if (newRsETHPrice > highestRsethPrice) {   // newRsETHPrice > 0 → true
Line 254:      uint256 priceDifference = newRsETHPrice - highestRsethPrice;  // = newRsETHPrice
Line 257:      bool isPriceIncreaseOffLimit =
                   pricePercentageLimit > 0 &&
                   priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
                   //                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                   //  = pricePercentageLimit * 0 / 1e18 = 0
                   //  so: priceDifference > 0  →  always true
Line 263:      if (!IAccessControl(...).hasRole(MANAGER, msg.sender)) {
Line 264:          revert PriceAboveDailyThreshold();   // ← always fires for non-managers
Line 265:      }
Line 266:  }
``` [1](#0-0) 

`WadMath.mulWad(x, 0)` computes `x * 0 / 1e18 = 0`, so the threshold is always zero when `highestRsethPrice = 0`. [2](#0-1) 

**Downstream impact — `contracts/LRTDepositPool.sol`, `getRsETHAmountToMint()`:**

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
//                                                                          ^^^^^^^^^^^
//  rsETHPrice = 0  →  division by zero  →  revert on every deposit
``` [3](#0-2) 

This is called from `_beforeDeposit`, which is called by both `depositETH` and `depositAsset`. [4](#0-3) 

---

### Impact Explanation

While `rsETHPrice = 0` and `highestRsethPrice = 0`:

1. Every call to `updateRSETHPrice()` (public) reverts with `PriceAboveDailyThreshold` — the price cannot be initialized by any non-manager.
2. Every call to `depositETH()` / `depositAsset()` reverts with division-by-zero because `rsETHPrice = 0`.
3. All user deposits are frozen until a manager calls `updateRSETHPriceAsManager()`.

**Scoped impact: Medium — Temporary freezing of funds.**

---

### Likelihood Explanation

The precondition is: a new `LRTOracle` proxy is deployed (not an in-place upgrade of the existing proxy) while rsETH `totalSupply > 0` and `pricePercentageLimit > 0`. This is a realistic migration scenario — the protocol is upgradeable and has already undergone multiple reinitializations (`reinitializer(2)` exists). Any redeployment of the oracle proxy without an immediate manager-only bootstrap call triggers the freeze. The normal fresh-deployment path (totalSupply = 0 on first call) is safe because lines 218–222 handle it, but the migration path is not. [5](#0-4) 

---

### Recommendation

Replace the no-op initialization guard with a sentinel that skips the threshold check on the very first price update when `highestRsethPrice` was unset:

```solidity
if (highestRsethPrice == 0) {
    // Bootstrap: set highestRsethPrice to the computed new price directly,
    // bypassing the threshold check, then return after updating rsETHPrice.
    highestRsethPrice = newRsETHPrice;  // computed after line 250
    rsETHPrice = newRsETHPrice;
    return;
}
```

Alternatively, guard the threshold check with `highestRsethPrice > 0`:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 &&
    highestRsethPrice > 0 &&          // ← add this guard
    priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

---

### Proof of Concept

```solidity
// Local fork / unit test (no mainnet)
function test_bootstrapRaceCondition() public {
    // Setup: deploy fresh LRTOracle proxy, rsETH already has supply (migration)
    uint256 rsethSupply = 1000 ether;
    mockRsETH.setTotalSupply(rsethSupply);          // totalSupply > 0
    lrtOracle.setPricePercentageLimit(1e16);        // 1% limit, > 0

    // rsETHPrice = 0, highestRsethPrice = 0 (uninitialized storage)
    assertEq(lrtOracle.rsETHPrice(), 0);
    assertEq(lrtOracle.highestRsethPrice(), 0);

    // Non-manager calls updateRSETHPrice() — should succeed on first call
    vm.prank(nonManager);
    vm.expectRevert(ILRTOracle.PriceAboveDailyThreshold.selector);
    lrtOracle.updateRSETHPrice();   // ← reverts: bug confirmed

    // Deposits also revert (rsETHPrice still 0)
    vm.expectRevert();              // division by zero
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
}
```

### Citations

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L224-266)
```text
        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/utils/WadMath.sol (L17-19)
```text
    function mulWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x.mulDiv(y, WAD);
    }
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-665)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```
