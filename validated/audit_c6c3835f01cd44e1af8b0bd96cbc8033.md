### Title
Public `updateRSETHPrice()` Triggers Automatic Pause of `LRTWithdrawalManager`, Temporarily Freezing Already-Unlocked Withdrawals — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function. When the computed rsETH price drops more than `pricePercentageLimit` below `highestRsethPrice`, `_updateRsETHPrice()` automatically calls `withdrawalManager.pause()`. Because `LRTWithdrawalManager.completeWithdrawal`, `initiateWithdrawal`, and `instantWithdrawal` all carry `whenNotPaused`, users with already-unlocked withdrawal requests are blocked from completing them until an admin manually calls `unpause()`.

---

### Finding Description

**Entrypoint — permissionless public function:**

`updateRSETHPrice()` carries no role guard: [1](#0-0) 

Any EOA or contract can call it at any time.

**Auto-pause logic on price drop:**

Inside `_updateRsETHPrice()`, when the freshly computed price falls more than `pricePercentageLimit` below `highestRsethPrice`, the function calls `pause()` on both the deposit pool and the withdrawal manager, then pauses itself and returns: [2](#0-1) 

The withdrawal manager's `pause()` requires `PAUSER_ROLE`. For the auto-pause mechanism to function as designed, the oracle contract must hold that role in production — which is the clear intent of the code.

**All user-facing withdrawal paths are gated by `whenNotPaused`:**

- `initiateWithdrawal` — [3](#0-2) 
- `completeWithdrawal` — [4](#0-3) 
- `instantWithdrawal` — [5](#0-4) 
- `unlockQueue` — [6](#0-5) 

**The critical gap:** `completeWithdrawal` is blocked even for requests that have already been unlocked (i.e., `nextLockedNonce` has already advanced past them and the withdrawal delay has already elapsed). Those users have already burned their rsETH at `unlockQueue` time and are simply waiting to collect their LST/ETH. The pause prevents that final step.

**Price drop precondition is realistic:**

`_getTotalEthInProtocol()` sums `getTotalAssetDeposits(asset) * assetPrice` across all supported assets: [7](#0-6) 

An EigenLayer slashing event that reduces NDC balances directly lowers `totalETHInProtocol`, which lowers `newRsETHPrice`. If the drop exceeds `pricePercentageLimit` relative to `highestRsethPrice`, the auto-pause fires.

**`unpause()` requires admin:** [8](#0-7) 

Until an admin acts, all withdrawal operations remain frozen.

---

### Impact Explanation

**Temporary freezing of funds (Medium).** Users who have already completed the withdrawal lifecycle — waited the delay, had their request unlocked by an operator, had their rsETH burned — cannot call `completeWithdrawal` to receive their LST/ETH. Their funds are locked in the contract until an admin unpauses. The freeze duration is unbounded from the user's perspective and depends entirely on admin response time.

---

### Likelihood Explanation

- EigenLayer slashing is a documented, non-negligible risk for any restaking protocol.
- `updateRSETHPrice()` is public; any actor (including a bot or a user who simply wants to update the price) can trigger the pause the moment a qualifying price drop exists on-chain.
- `pricePercentageLimit` is a single threshold that applies symmetrically to both upward and downward moves; a modest slashing event combined with a tight limit is sufficient.
- No front-running or privileged access is required.

---

### Recommendation

1. **Exempt `completeWithdrawal` from the pause**, or introduce a separate `withdrawalCompletionPaused` flag so that already-unlocked requests can always be finalized regardless of oracle-triggered pauses.
2. Alternatively, restrict `updateRSETHPrice()` to a role (e.g., `MANAGER`) so that the auto-pause cannot be triggered by an arbitrary caller. The existing `updateRSETHPriceAsManager()` already provides a privileged path; making the public variant role-gated removes the permissionless trigger.
3. Consider separating the "pause new deposits/initiations" concern from the "pause completions" concern at the contract level.

---

### Proof of Concept

```solidity
// Fork test (Foundry) — unmodified production contracts
function test_publicUpdatePausesCompleteWithdrawal() external {
    // 1. User initiates and operator unlocks a withdrawal (rsETH already burned)
    vm.prank(user);
    withdrawalManager.initiateWithdrawal(asset, rsETHAmount, "");
    vm.roll(block.number + withdrawalManager.withdrawalDelayBlocks() + 1);
    vm.prank(operator);
    withdrawalManager.unlockQueue(asset, type(uint256).max, 0, type(uint256).max, 0, type(uint256).max);

    // 2. Simulate EigenLayer slashing: reduce NDC balance so getTotalAssetDeposits drops
    //    enough that newRsETHPrice < highestRsethPrice * (1 - pricePercentageLimit)
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

**File:** contracts/LRTWithdrawalManager.sol (L150-158)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L212-220)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
```

**File:** contracts/LRTWithdrawalManager.sol (L268-281)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
```

**File:** contracts/LRTWithdrawalManager.sol (L352-354)
```text
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```
