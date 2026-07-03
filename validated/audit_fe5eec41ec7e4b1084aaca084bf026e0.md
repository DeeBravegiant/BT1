Audit Report

## Title
Publicly Callable `updateRSETHPrice()` and `FeeReceiver.sendFunds()` Enable Sandwich Attack to Steal Yield from rsETH Holders — (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` carries no access control and `FeeReceiver.sendFunds()` is similarly unrestricted. An attacker can observe accumulated MEV rewards sitting in `FeeReceiver`, deposit at the stale (pre-reward) `rsETHPrice`, atomically flush the rewards into the deposit pool, then trigger the price update — capturing a share of yield that belongs to pre-existing rsETH holders. The 8-day withdrawal delay defers but does not eliminate the profit.

## Finding Description

**Root cause — no access control on price-sensitive public functions:**

`LRTOracle.updateRSETHPrice()` is callable by anyone: [1](#0-0) 

`FeeReceiver.sendFunds()` is callable by anyone: [2](#0-1) 

Deposits use the cached `rsETHPrice` directly: [3](#0-2) 

Withdrawal sizing also uses the cached `rsETHPrice`: [4](#0-3) 

**Exploit flow:**

The price update computes `previousTVL = rsethSupply × rsETHPrice`. When the attacker deposits before the price update, their deposit increases `rsethSupply`, which raises the `previousTVL` baseline and reduces the computed `rewardAmount` attributed to pre-existing holders: [5](#0-4) 

**Why the `pricePercentageLimit` guard is insufficient:**

The guard only reverts if the price increase exceeds the configured threshold. When rewards are within the band, the attack proceeds unimpeded. When `pricePercentageLimit == 0`, the check is skipped entirely: [6](#0-5) 

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders accumulate yield through staking rewards and MEV fees. An attacker who deposits just before triggering the price update dilutes those holders' share of the pending reward. The attacker exits with more ETH than they deposited; the shortfall comes directly from yield owed to pre-existing holders. The attack scales linearly with deposit size and accumulated reward magnitude.

## Likelihood Explanation

**Medium.** MEV/execution-layer rewards accumulate in `FeeReceiver` continuously and are publicly visible on-chain. No privileged role is required for any step. The only friction is the 8-day withdrawal delay, which defers but does not prevent profit. The `pricePercentageLimit` guard reduces per-transaction profit but does not eliminate the attack, and is fully bypassable when the limit is unset.

## Recommendation

1. **Atomically refresh the price on every deposit and withdrawal.** Call `_updateRsETHPrice()` at the start of `depositETH`, `depositAsset`, and `initiateWithdrawal` so the price used for minting/sizing always reflects current TVL.
2. **Restrict `FeeReceiver.sendFunds()` to an authorized role** (e.g., `MANAGER`) so reward injection cannot be weaponised as part of a sandwich.
3. As defence-in-depth, enforce a minimum deposit lock-up period to prevent same-block or same-transaction deposit-then-withdraw cycles.

## Proof of Concept

**Setup:**
- Protocol TVL = 10,000 ETH; rsETH supply = 10,000; `rsETHPrice` = 1.0 ETH (stale).
- `FeeReceiver` holds 100 ETH in accumulated MEV rewards.
- Protocol fee = 10%.

**Attack steps (single transaction or sequential blocks):**

1. Attacker calls `LRTDepositPool.depositETH{value: 1000 ETH}(...)`.
   - `rsethAmountToMint = 1000 × 1.0 / 1.0 = 1000 rsETH` (stale price used).
   - New supply = 11,000 rsETH; TVL = 11,000 ETH.

2. Attacker calls `FeeReceiver.sendFunds()`.
   - 100 ETH moves to deposit pool; TVL = 11,100 ETH.

3. Attacker calls `LRTOracle.updateRSETHPrice()`.
   - `previousTVL = 11,000 × 1.0 = 11,000 ETH`
   - `rewardAmount = 100 ETH`; `protocolFeeInETH = 10 ETH`
   - `newRsETHPrice = (11,100 − 10) / 11,000 ≈ 1.00818 ETH`

4. After the 8-day delay, attacker redeems 1,000 rsETH ≈ 1,008.18 ETH. **Profit ≈ 8.18 ETH.**

**Counterfactual (no attack):** Without the attacker's deposit, `newRsETHPrice = (10,100 − 10) / 10,000 = 1.009 ETH`. Existing holders lose ~0.082% yield per attack cycle, with the shortfall transferred directly to the attacker.

**Foundry test plan:** Fork mainnet, seed `FeeReceiver` with ETH, call `depositETH` → `sendFunds` → `updateRSETHPrice` in sequence, assert attacker's rsETH redemption value exceeds deposit, and assert existing holders' per-rsETH value is lower than the no-attack baseline.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L234-246)
```text
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
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
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

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
