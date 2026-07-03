Audit Report

## Title
Missing `totalETHInProtocol == 0` Guard Causes Spurious Auto-Pause When Treasury Holds Fee rsETH — (`contracts/LRTOracle.sol`)

## Summary

`_updateRsETHPrice()` guards only the `rsethSupply == 0` case. When `totalETHInProtocol == 0` with `rsethSupply > 0` (reachable after all user rsETH is burned while treasury-minted fee rsETH remains outstanding), `newRsETHPrice` computes to `0`. This 100% price drop exceeds any non-zero `pricePercentageLimit`, triggering the auto-pause on `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` — with no actual slashing having occurred.

## Finding Description

**Root cause — `_updateRsETHPrice()`, `contracts/LRTOracle.sol`**

The only early-exit guard checks `rsethSupply == 0`:

```solidity
// LRTOracle.sol L218-222
if (rsethSupply == 0) {
    rsETHPrice = 1 ether;
    highestRsethPrice = 1 ether;
    return;
}
```

There is no corresponding guard for `totalETHInProtocol == 0` when `rsethSupply > 0`.

When `totalETHInProtocol == 0`, `protocolFeeInETH` is also `0` (the fee branch at L244 requires `totalETHInProtocol > previousTVL`, which cannot hold when `totalETHInProtocol == 0` and `rsethSupply > 0` implies `previousTVL > 0`). The price computation at L250 then yields:

```solidity
uint256 newRsETHPrice = (0 - 0).divWad(rsethSupply); // == 0
```

The downside-protection block at L270–281 then evaluates:

```solidity
if (newRsETHPrice < highestRsethPrice) {          // 0 < highestRsethPrice → true
    uint256 diff = highestRsethPrice - 0;          // == highestRsethPrice (100% drop)
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 &&
        diff > pricePercentageLimit.mulWad(highestRsethPrice); // true for any limit < 100%
    if (isPriceDecreaseOffLimit) {
        lrtDepositPool.pause();
        withdrawalManager.pause();
        _pause();
        return;
    }
}
```

A 100% drop exceeds any `pricePercentageLimit < 1e18`, so `isPriceDecreaseOffLimit` is `true` and the protocol is paused.

**How `totalETHInProtocol == 0` with `rsethSupply > 0` is reached**

`_getTotalEthInProtocol()` (L331–349) sums `getTotalAssetDeposits(asset)` for every supported asset. `getTotalAssetDeposits` (L385–397 of `LRTDepositPool.sol`) covers the deposit pool, all NDCs, EigenLayer staked + unstaking, converter, and unstaking vault.

The realistic path:
1. Protocol earns staking rewards; `updateRSETHPrice()` is called with `totalETHInProtocol > previousTVL`.
2. Fee rsETH is minted to the treasury (L304–307): `IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee)`.
3. All non-treasury users initiate and complete withdrawals, burning their rsETH. Treasury fee rsETH is **never burned** during user withdrawals.
4. After all user withdrawals settle: every asset location holds zero. `rsethSupply = treasury_fee_rsETH > 0`.
5. Any caller invokes the public `updateRSETHPrice()` (L87–89).
6. `newRsETHPrice = 0` → `isPriceDecreaseOffLimit = true` → auto-pause fires.

A secondary path: integer rounding in the rsETH burn calculation leaves 1 wei of rsETH outstanding after the last withdrawal, with zero backing assets.

## Impact Explanation

**Medium — Temporary freezing of funds.**

Once the auto-pause fires, `LRTDepositPool` and `LRTWithdrawalManager` are paused, blocking all deposits and withdrawals. `LRTOracle` itself is also paused, preventing any price update. Users with pending withdrawal requests cannot claim their assets until an admin manually calls `unpause()` on each contract. No funds are permanently lost, but access is frozen until admin intervention.

## Likelihood Explanation

Moderate-low. The scenario requires the treasury to hold fee rsETH (which occurs whenever the protocol earns rewards and `updateRSETHPrice()` is called with a TVL increase) and for all user-held rsETH to be burned through withdrawals. This is an extreme but reachable state — particularly relevant during protocol wind-down or after a large coordinated exit. No admin compromise, front-running, or external oracle manipulation is required. `updateRSETHPrice()` is `public` and callable by any EOA.

## Recommendation

Add an explicit guard in `_updateRsETHPrice()` immediately after the `rsethSupply == 0` check:

```solidity
if (totalETHInProtocol == 0) {
    // No assets backing rsETH; skip price update to avoid spurious pause.
    return;
}
```

Alternatively, revert instead of silently returning so operators are alerted to the anomalous state. Also consider whether treasury-held fee rsETH should be excluded from `rsethSupply` when computing the price, or whether the treasury should periodically redeem its rsETH to keep the backing ratio consistent.

## Proof of Concept

```solidity
// Foundry pseudocode — wire up real mocks for a local test

function test_zeroPriceTriggersPause() public {
    // Preconditions:
    // - rsETHPrice = 1 ether, highestRsethPrice = 1 ether
    // - rsethSupply = 1 wei (treasury fee rsETH minted in a prior updateRSETHPrice call)
    // - All asset balances across DepositPool / NDCs / EigenLayer / UnstakingVault = 0
    // - pricePercentageLimit = 5e16 (5%)

    assertEq(rseth.totalSupply(), 1);           // 1 wei rsETH in treasury
    assertEq(oracle.highestRsethPrice(), 1 ether);
    // getTotalAssetDeposits returns 0 for all supported assets

    // Any EOA calls updateRSETHPrice()
    vm.prank(address(0xdead));
    oracle.updateRSETHPrice();

    // Auto-pause fired
    assertTrue(oracle.paused());
    assertTrue(depositPool.paused());
    assertTrue(withdrawalManager.paused());
}
```

The test is locally reproducible on unmodified contracts with mocked asset balances set to zero and `rsethSupply` set to 1 wei via a prior fee-mint call.