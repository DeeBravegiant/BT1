Audit Report

## Title
Paused `LRTWithdrawalManager` Prevents Users from Completing Pending Withdrawals, Temporarily Freezing rsETH-Backed Funds - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager` gates all withdrawal completion paths (`completeWithdrawal`, `completeWithdrawalForUser`, `unlockQueue`) with `whenNotPaused`. After a user calls `initiateWithdrawal()` and transfers rsETH into the contract, a public caller can trigger an automatic pause via `LRTOracle.updateRSETHPrice()` if the rsETH price drops beyond `pricePercentageLimit`, blocking the user from ever completing their withdrawal until an admin manually unpauses. This constitutes a temporary freeze of user funds.

## Finding Description
The exploit path is fully supported by the code:

**Step 1 — rsETH committed:** `initiateWithdrawal()` immediately transfers the user's rsETH into the contract: [1](#0-0) 

**Step 2 — Automatic pause trigger:** `updateRSETHPrice()` is `public` with no access control, callable by anyone: [2](#0-1) 

When the computed price drops beyond `pricePercentageLimit` relative to `highestRsethPrice`, `_updateRsETHPrice()` unconditionally pauses the withdrawal manager: [3](#0-2) 

**Step 3 — All completion paths blocked:** Once paused, every path a user could use to recover their funds is gated:

- `completeWithdrawal()` — `whenNotPaused` at line 183: [4](#0-3) 

- `completeWithdrawalForUser()` — `whenNotPaused` at line 199: [5](#0-4) 

- `unlockQueue()` — `whenNotPaused` at line 279, blocking even the operator-side processing step: [6](#0-5) 

The only recovery path is an admin calling `unpause()` on `LRTWithdrawalManager`, which requires privileged action with no time bound.

## Impact Explanation
**Medium — Temporary freezing of funds.** Users who have already submitted `initiateWithdrawal()` and transferred rsETH into the contract cannot retrieve their underlying ETH/LST for the entire duration of the pause. The rsETH is not permanently lost, but the freeze is indefinite (no automatic unpause mechanism exists) and occurs precisely during market stress when users most urgently need liquidity. This matches the allowed impact class "Temporary freezing of funds."

## Likelihood Explanation
**Medium.** The trigger requires no privileged role. Any external account can call `updateRSETHPrice()`. The pause fires automatically if the price condition (`diff > pricePercentageLimit * highestRsethPrice`) is satisfied — a realistic scenario during market downturns. The `pricePercentageLimit` is admin-configurable, but once set, the trigger is fully permissionless. The scenario is most likely during exactly the conditions (price stress, high withdrawal demand) when users are most actively trying to complete withdrawals.

## Recommendation
Remove `whenNotPaused` from `completeWithdrawal()` and `completeWithdrawalForUser()` so users with already-queued, already-unlocked requests can always claim their assets. `initiateWithdrawal()` and `instantWithdrawal()` may reasonably remain paused to block new commitments. `unlockQueue()` should similarly remain callable while paused so operator processing is not blocked.

```diff
- function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
+ function completeWithdrawal(address asset, string calldata referralId) external nonReentrant {

  function completeWithdrawalForUser(
      address asset,
      address user,
      string calldata referralId
- ) external nonReentrant whenNotPaused onlyLRTOperator {
+ ) external nonReentrant onlyLRTOperator {
```

## Proof of Concept
1. User calls `initiateWithdrawal(asset, rsETHAmount, referralId)`. rsETH is transferred into `LRTWithdrawalManager` at line 166.
2. Operator calls `unlockQueue()` after `withdrawalDelayBlocks` (~8 days), burning the rsETH and moving the request to unlocked state (lines 301–305).
3. rsETH price drops sharply. Any external account calls `updateRSETHPrice()`. `_updateRsETHPrice()` computes `isPriceDecreaseOffLimit = true` and calls `withdrawalManager.pause()` (lines 277–281 of `LRTOracle.sol`).
4. User calls `completeWithdrawal()` — reverts with `ContractPaused` due to `whenNotPaused` at line 183.
5. The underlying ETH/LST is held in the contract and inaccessible. No time-bound unpause exists; only an admin calling `unpause()` (line 352–354 of `LRTWithdrawalManager.sol`) can unblock the user.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L183-184)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
```

**File:** contracts/LRTWithdrawalManager.sol (L192-204)
```text
    function completeWithdrawalForUser(
        address asset,
        address user,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
        _processWithdrawalCompletion(asset, user, referralId);
        emit AssetWithdrawalCompletedBy(msg.sender);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L276-281)
```text
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L277-281)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```
