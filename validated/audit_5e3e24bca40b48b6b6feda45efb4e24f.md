### Title
FIFO Queue Head Blocking Causes Temporary Freezing of All Pending Withdrawals - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

In `LRTWithdrawalManager`, the `_unlockWithdrawalRequests` function processes the withdrawal queue in strict FIFO order and unconditionally `break`s when the head request's payout exceeds the vault's available balance. Any withdrawal request at the front of the queue that cannot be immediately satisfied blocks every subsequent request from being unlocked, regardless of their individual sizes.

---

### Finding Description

`_unlockWithdrawalRequests` iterates from `nextLockedNonce` upward and exits the loop the moment a single request cannot be covered:

```solidity
// contracts/LRTWithdrawalManager.sol  line 800
if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
``` [1](#0-0) 

The `break` is unconditional — it does not skip the oversized request and continue to smaller ones behind it. Because `nextLockedNonce` is only advanced for successfully unlocked requests, the oversized request permanently occupies the head of the queue until the vault accumulates enough assets to cover it.

The `availableAssetAmount` passed into this function comes from `unstakingVault.balanceOf(asset)` — the raw ETH/LST balance of the `LRTUnstakingVault`:

```solidity
// contracts/LRTWithdrawalManager.sol  line 849
totalAvailableAssets: unstakingVault.balanceOf(asset)
``` [2](#0-1) 

This is structurally different from the `assetsCommitted`-based check used at queue time in `initiateWithdrawal`:

```solidity
// contracts/LRTWithdrawalManager.sol  line 170
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
``` [3](#0-2) 

`getAvailableAssetAmount` uses `lrtDepositPool.getTotalAssetDeposits(asset)` — the total protocol-wide accounting figure — while `unlockQueue` uses the vault's live balance, which only contains assets explicitly moved there after EigenLayer withdrawal completion. A large withdrawal can be validly queued (passing the deposit-pool check) while the vault holds far less than the committed amount. [4](#0-3) 

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

All users whose withdrawal requests sit behind the oversized head request have their rsETH locked inside `LRTWithdrawalManager` (transferred in at `initiateWithdrawal`) and cannot call `completeWithdrawal` until `nextLockedNonce` advances past the blocking request. The operator cannot skip the blocking request: `unlockQueue` only accepts an upper-bound index (`firstExcludedIndex`), not a lower-bound skip. The freeze persists until the vault accumulates enough assets to cover the large request, which may require multiple EigenLayer withdrawal cycles. [5](#0-4) 

---

### Likelihood Explanation

**Medium.** The condition arises naturally in normal protocol operation:

1. A whale legitimately calls `initiateWithdrawal` for a large rsETH amount. The deposit-pool check passes because total protocol assets are large.
2. Smaller users queue withdrawals after the whale.
3. The operator initiates EigenLayer unstaking and calls `unlockQueue` once assets arrive in the vault — but the vault balance covers only the smaller requests, not the whale's.
4. The `break` fires on the whale's request; all smaller requests behind it are frozen.

No special privileges, front-running, or oracle manipulation are required. The attacker path is simply calling the public `initiateWithdrawal` function with a large amount. [6](#0-5) 

---

### Recommendation

Replace the `break` with a `continue` (or equivalent skip logic) so that requests the vault cannot currently cover are skipped rather than halting the entire queue. A common pattern is to mark oversized requests as "pending" and resume from the next request, updating `nextLockedNonce` only for successfully processed entries. Alternatively, allow operators to pass an explicit skip-list of nonces to bypass temporarily unserviceable requests.

---

### Proof of Concept

1. Protocol has 1000 ETH worth of stETH in total deposits; vault holds 100 stETH.
2. **Whale** calls `initiateWithdrawal(stETH, rsETH_for_900_stETH)`. Check at line 170 passes (900 < 1000 − 0). `assetsCommitted[stETH] = 900`.
3. **Alice** calls `initiateWithdrawal(stETH, rsETH_for_10_stETH)`. Check passes (10 < 1000 − 900 = 100). `assetsCommitted[stETH] = 910`. Whale's request is nonce 0; Alice's is nonce 1.
4. Operator calls `unlockQueue(stETH, 2, ...)`. `totalAvailableAssets = unstakingVault.balanceOf(stETH) = 100`.
5. Loop iteration 0: `payoutAmount ≈ 900`. `100 < 900` → **`break`**. Loop exits. `nextLockedNonce` stays at 0.
6. Alice's request (nonce 1, needing only 10 stETH) is never reached. Her rsETH remains locked in the contract with no path to `completeWithdrawal` until the vault accumulates ≥ 900 stETH. [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
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
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L268-303)
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
    {
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L790-815)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```

**File:** contracts/LRTWithdrawalManager.sol (L837-851)
```text
    function _createUnlockParams(
        ILRTOracle lrtOracle,
        ILRTUnstakingVault unstakingVault,
        address asset
    )
        internal
        view
        returns (UnlockParams memory)
    {
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```
