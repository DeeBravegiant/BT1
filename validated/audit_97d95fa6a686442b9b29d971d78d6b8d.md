Audit Report

## Title
Public `updateRSETHPrice()` Triggers Automatic Pause That Freezes Already-Unlocked Withdrawals — (`contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is callable by any address with no role restriction. When the computed price drops beyond `pricePercentageLimit`, it automatically calls `withdrawalManager.pause()`. Because `completeWithdrawal` carries `whenNotPaused`, users whose rsETH was already burned during `unlockQueue` cannot retrieve the assets owed to them until an admin manually calls `unpause()`.

## Finding Description

**Public entrypoint with no role check:**
`LRTOracle.updateRSETHPrice()` is declared `public whenNotPaused` with no role restriction, meaning any EOA or contract can invoke it. [1](#0-0) 

**Automatic pause on price drop:**
Inside `_updateRsETHPrice()`, when `newRsETHPrice < highestRsethPrice` and the difference exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`, the function calls `withdrawalManager.pause()` and `_pause()` on the oracle, then returns without updating `rsETHPrice`. [2](#0-1) 

**Price derived from live on-chain state:**
`_getTotalEthInProtocol()` reads `ILRTDepositPool.getTotalAssetDeposits(asset)` for every supported asset. An EigenLayer slashing event that reduces NDC balances directly lowers `newRsETHPrice` on the next call. [3](#0-2) 

**`completeWithdrawal` gated by `whenNotPaused`:**
Once `withdrawalManager.pause()` is called, every user-facing withdrawal completion path reverts. [4](#0-3) 

**rsETH burned before `completeWithdrawal` is called:**
In `unlockQueue`, rsETH is burned from the contract and assets are redeemed from the vault atomically. After this point, the user's rsETH is gone and the owed assets sit inside `LRTWithdrawalManager`. A subsequent pause leaves those users unable to claim assets they are already owed. [5](#0-4) 

**Exploit path:**
1. User calls `initiateWithdrawal`; operator calls `unlockQueue` → rsETH burned, assets redeemed into `LRTWithdrawalManager`.
2. EigenLayer slashing reduces `getTotalAssetDeposits`, lowering `newRsETHPrice` below the `pricePercentageLimit` threshold.
3. Any unprivileged caller invokes `lrtOracle.updateRSETHPrice()`.
4. `_updateRsETHPrice()` calls `withdrawalManager.pause()`.
5. User calls `completeWithdrawal` → reverts with `Pausable: paused`.
6. Funds remain frozen in `LRTWithdrawalManager` until admin calls `unpause()`.

**Existing checks are insufficient:**
The `whenNotPaused` guard on `updateRSETHPrice()` only prevents calls when the oracle is already paused; it does not restrict who can trigger the pause. The `pricePercentageLimit` threshold is a finite admin-set value that a sufficiently large slashing event will cross.

## Impact Explanation

Users who have passed through `unlockQueue` (rsETH burned, assets redeemed into the contract) cannot call `completeWithdrawal` while the contract is paused. Their funds are frozen inside `LRTWithdrawalManager` until an admin calls `unpause()`. This constitutes **temporary freezing of funds**, a Medium-severity impact per the allowed scope.

## Likelihood Explanation

- `updateRSETHPrice()` requires no privilege; any EOA or bot can call it at any time.
- EigenLayer slashing is a documented, realistic risk for LST-backed protocols.
- `pricePercentageLimit` is a finite threshold; a sufficiently large slashing event will cross it.
- No front-running or brute-force is required — the caller simply invokes the public function after slashing has already reduced `getTotalAssetDeposits`.
- The condition is repeatable: every time slashing pushes the price below the threshold, any caller can re-trigger the pause after an admin unpauses.

## Recommendation

Introduce a separate withdrawal completion path that does not carry `whenNotPaused` but is restricted to requests whose nonce is below `nextLockedNonce[asset]` (i.e., requests that were already unlocked before the pause). Since rsETH for these requests has already been burned and the assets are already held in the contract, there is no economic risk in allowing their completion while paused. One concrete approach: add a `completeUnlockedWithdrawal` function that checks `usersFirstWithdrawalRequestNonce < nextLockedNonce[asset]` and omits the `whenNotPaused` modifier.

## Proof of Concept

```solidity
// Foundry fork test
function test_pauseFreezesUnlockedWithdrawals() public {
    // 1. User initiates withdrawal; operator calls unlockQueue
    //    → rsETH burned, assets redeemed into LRTWithdrawalManager
    vm.prank(operator);
    withdrawalManager.unlockQueue(
        asset, type(uint256).max, 0, type(uint256).max, 0, type(uint256).max
    );

    // 2. Simulate EigenLayer slashing: reduce NDC balance so
    //    getTotalAssetDeposits drops enough to push newRsETHPrice
    //    below (highestRsethPrice - pricePercentageLimit * highestRsethPrice)
    _simulateSlashing(slashAmount);

    // 3. Any unprivileged caller triggers the price update
    vm.prank(address(0xdead));
    lrtOracle.updateRSETHPrice();

    // 4. Withdrawal manager is now paused
    assertTrue(withdrawalManager.paused());

    // 5. User cannot complete their already-unlocked withdrawal
    vm.prank(user);
    vm.expectRevert("Pausable: paused");
    withdrawalManager.completeWithdrawal(asset, "");
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```
