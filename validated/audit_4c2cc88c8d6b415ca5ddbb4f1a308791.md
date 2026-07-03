Audit Report

## Title
Publicly Callable `updateRSETHPrice()` and `FeeReceiver.sendFunds()` Enable Yield Theft from rsETH Holders — (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` carries no access control and is callable by any address. `FeeReceiver.sendFunds()` is similarly unrestricted. An attacker can deposit at a stale (pre-reward) price, atomically inject accumulated MEV rewards via `sendFunds()`, then trigger the price update — capturing a share of yield that belongs to pre-existing rsETH holders. The shortfall is borne directly by existing holders whose yield is diluted.

## Finding Description

**Root cause — three unrestricted public entry points:**

`LRTOracle.updateRSETHPrice()` has no role guard:
```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`FeeReceiver.sendFunds()` has no access control:
```solidity
// contracts/FeeReceiver.sol:53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

**Stale price used verbatim at deposit time:**
```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**Stale price used verbatim at withdrawal initiation:**
```solidity
// contracts/LRTWithdrawalManager.sol:593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**Price update computes reward against a baseline that includes the attacker's deposit:**
```solidity
// contracts/LRTOracle.sol:234,244-246
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
...
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

Because the attacker's deposit increases `rsethSupply` before the price update, `previousTVL` rises, which reduces `rewardAmount` attributed to pre-existing holders. The attacker's newly minted rsETH participates in the price appreciation as if they had been a holder before the rewards arrived.

**`pricePercentageLimit` guard is insufficient:**
```solidity
// contracts/LRTOracle.sol:256-257
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```
When `pricePercentageLimit == 0` the check is entirely skipped. When set, it only caps per-transaction profit but does not prevent the attack — it can be repeated across multiple blocks.

**Exploit path (single atomic transaction via attacker contract):**
1. Observe `FeeReceiver` balance on-chain (e.g., 100 ETH accumulated MEV rewards).
2. Call `LRTDepositPool.depositETH{value: 1000 ETH}()` — minted at stale price 1.0, receives 1,000 rsETH. Supply becomes 11,000; TVL = 11,000 ETH.
3. Call `FeeReceiver.sendFunds()` — 100 ETH moves to deposit pool; TVL = 11,100 ETH.
4. Call `LRTOracle.updateRSETHPrice()`:
   - `previousTVL = 11,000 × 1.0 = 11,000 ETH`
   - `rewardAmount = 100 ETH`; `protocolFeeInETH = 10 ETH`
   - `newRsETHPrice = (11,100 − 10) / 11,000 ≈ 1.00818`
5. Attacker's 1,000 rsETH is now worth ≈ 1,008.18 ETH — a profit of ≈ 8.18 ETH.

Without the attack, original holders would have received ≈ 90 ETH of yield (after 10% fee). With the attack, they receive only ≈ 81.8 ETH — the ≈ 8.18 ETH difference is transferred to the attacker. The attack scales linearly with deposit size and accumulated reward magnitude.

## Impact Explanation

**High — Theft of unclaimed yield.** Existing rsETH holders accumulate yield through staking rewards and MEV fees. The attacker extracts a portion of that yield by depositing at a stale price and controlling the timing of both reward injection and price update. The stolen amount comes directly from yield owed to pre-existing holders and is proportional to the attacker's deposit relative to total supply and the size of accumulated rewards.

## Likelihood Explanation

**Medium.** `FeeReceiver` accumulates MEV/execution-layer rewards continuously and its balance is publicly visible on-chain. No privileged role is required for any step. The attack can be executed atomically in a single transaction. The 8-day withdrawal delay defers but does not prevent profit. The `pricePercentageLimit` guard reduces per-transaction yield theft but does not eliminate it, and is entirely absent when `pricePercentageLimit == 0`. The attack is repeatable every time rewards accumulate.

## Recommendation

1. **Atomically refresh the price on every deposit and withdrawal.** Call `_updateRsETHPrice()` at the start of `depositETH`, `depositAsset`, and `initiateWithdrawal` so the price used for minting/sizing always reflects current TVL before the user's action is applied.
2. **Restrict `FeeReceiver.sendFunds()` to an authorized role** (e.g., `MANAGER`) so reward injection cannot be weaponised as part of a sandwich.
3. As defence-in-depth, enforce a minimum deposit lock-up period to prevent same-block deposit-then-price-update cycles.

## Proof of Concept

**Foundry fork test outline:**

```solidity
function testYieldTheft() public {
    // Setup: protocol has 10_000 ETH TVL, 10_000 rsETH supply, rsETHPrice = 1e18
    // FeeReceiver holds 100 ETH in accumulated MEV rewards

    vm.startPrank(attacker);

    // Step 1: deposit at stale price
    depositPool.depositETH{value: 1000 ether}(0, "");
    // attacker receives 1000 rsETH (stale price = 1.0)

    // Step 2: inject rewards
    feeReceiver.sendFunds();
    // TVL now = 11_100 ETH

    // Step 3: trigger price update
    lrtOracle.updateRSETHPrice();
    // newRsETHPrice ≈ 1.00818e18

    vm.stopPrank();

    // Assert: attacker's rsETH value > 1000 ETH deposited
    uint256 attackerRsETH = rsETH.balanceOf(attacker);
    uint256 attackerValue = attackerRsETH * lrtOracle.rsETHPrice() / 1e18;
    assertGt(attackerValue, 1000 ether); // ~1008.18 ETH

    // Assert: original holders received less yield than without the attack
    // (original 10_000 rsETH worth ~10_081.8 ETH instead of ~10_090 ETH)
}
```