Audit Report

## Title
Reward Sandwiching via Permissionless `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` Enables Yield Theft from rsETH Holders - (File: contracts/FeeReceiver.sol, contracts/LRTOracle.sol)

## Summary
`FeeReceiver.sendFunds()` carries no access control, and `LRTOracle.updateRSETHPrice()` is a public function with no role restriction. An attacker can deposit ETH at the stale pre-reward `rsETHPrice`, atomically push accumulated MEV rewards into the protocol and trigger a price update, then immediately initiate a withdrawal at the inflated post-reward price. After the 8-day delay, the attacker exits with a profit equal to their proportional share of the rewards, stealing yield that belonged to pre-existing rsETH holders.

## Finding Description

**Root cause — two permissionless state-changing functions:**

`FeeReceiver.sendFunds()` is `external` with no role modifier: [1](#0-0) 

`LRTOracle.updateRSETHPrice()` is `public whenNotPaused` with no role check: [2](#0-1) 

**Deposit minting uses the lazily-stored `rsETHPrice`:** [3](#0-2) 

**Withdrawal initiation also reads the stored `rsETHPrice` at call time, locking in `expectedAssetAmount`:** [4](#0-3) [5](#0-4) 

**`_calculatePayoutAmount` at `unlockQueue` time takes the minimum of the initiation-time `expectedAssetAmount` and the current return**, so the attacker's post-reward locked-in amount is preserved as long as the price does not fall below it: [6](#0-5) 

**Exploit flow:**
1. Attacker calls `LRTDepositPool.depositETH{value: D}()` — minted rsETH uses the current stale (pre-reward) `rsETHPrice`.
2. Attacker calls `FeeReceiver.sendFunds()` — accumulated MEV/EL rewards (amount `R`) are pushed to the deposit pool, increasing `totalETHInProtocol`.
3. Attacker calls `LRTOracle.updateRSETHPrice()` — stored `rsETHPrice` is updated to reflect the new TVL.
4. Attacker calls `LRTWithdrawalManager.initiateWithdrawal(ETH, rsETHAmount, "")` — `expectedAssetAmount` is computed at the inflated post-reward price and stored in the withdrawal request.
5. After 8 days, the operator calls `unlockQueue`; `_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)`. Since the attacker locked in the post-reward price, they receive `≈ D + (D / (TVL + D)) × R`.

**Why the `pricePercentageLimit` guard is insufficient:**

The guard at `_updateRsETHPrice` only reverts non-manager callers when the price increase exceeds `pricePercentageLimit × highestRsethPrice`: [7](#0-6) 

This does not prevent the attack when: (a) `pricePercentageLimit` is set to `0` (the check is skipped entirely per `pricePercentageLimit > 0 &&`), or (b) the accumulated reward represents a price increase within the configured threshold — which is the normal case for daily MEV accumulation. The attacker can also time the attack to trigger just before the threshold is breached.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders earn yield through `rsETHPrice` appreciation as rewards accrue. An attacker who deposits just before rewards are recognized and withdraws immediately after captures `(D / (TVL + D)) × R` of those rewards without having been exposed to the protocol during the period the rewards were earned. This is a direct, repeatable dilution of yield for all pre-existing holders. The profit scales with deposit size and reward amount, and the only cost is the 8-day opportunity cost of capital.

## Likelihood Explanation

**Medium.** The attack requires only: (1) capital to deposit (returned after 8 days), (2) monitoring `FeeReceiver.balance` on-chain (trivial), and (3) calling three permissionless public functions in sequence. No privileged access, no victim cooperation, and no external oracle compromise is needed. The 8-day delay is friction but not a deterrent for a well-capitalized attacker. The attack is repeatable every reward cycle.

## Recommendation

1. **Restrict `FeeReceiver.sendFunds()`** to a privileged role (e.g., `MANAGER`) so reward distribution cannot be triggered by an arbitrary caller.
2. **Introduce a minimum deposit holding period** (e.g., matching or exceeding the withdrawal delay) before a depositor can initiate a withdrawal, preventing same-block or same-transaction sandwiching.
3. **Alternatively, stream rewards** linearly over time rather than crediting the full reward amount in a single `updateRSETHPrice()` call, so no single block can capture a disproportionate share of accumulated yield.

## Proof of Concept

Assume: TVL = 10,000 ETH, rsETH supply = 10,000, `rsETHPrice` = 1.0 ETH/rsETH, `FeeReceiver` holds 10 ETH in accumulated MEV rewards.

```
// Step 1: Deposit at stale price
LRTDepositPool.depositETH{value: 1000 ether}(0, "");
// Attacker receives 1000 rsETH (1000/1.0). TVL = 11,000 ETH, supply = 11,000.

// Step 2: Push rewards into protocol
FeeReceiver.sendFunds();
// TVL = 11,010 ETH, supply = 11,000 rsETH.

// Step 3: Update price
LRTOracle.updateRSETHPrice();
// rsETHPrice = 11,010 / 11,000 ≈ 1.000909 ETH/rsETH.

// Step 4: Initiate withdrawal at inflated price
LRTWithdrawalManager.initiateWithdrawal(ETH_TOKEN, 1000 ether, "");
// expectedAssetAmount = 1000 * 1.000909 / 1.0 ≈ 1000.909 ETH (locked in request).

// Step 5: After 8 days, operator calls unlockQueue, then:
LRTWithdrawalManager.completeWithdrawal(ETH_TOKEN, "");
// Attacker receives ≈ 1000.909 ETH.
// Profit ≈ 0.909 ETH = (1000/11000) × 10 ETH stolen from existing holders.
```

A Foundry fork test can reproduce this by: forking mainnet, seeding `FeeReceiver` with ETH, executing the four-call sequence in a single transaction (steps 1–4), advancing 8 days with `vm.roll`, having the operator call `unlockQueue`, and asserting the attacker's final ETH balance exceeds their initial deposit.

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L168-175)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
