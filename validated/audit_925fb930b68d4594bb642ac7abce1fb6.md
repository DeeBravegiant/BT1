### Title
`instantWithdrawal` in `LRTWithdrawalManager.sol` Reverts for stETH Due to Assumed 1:1 Transfer Return — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal()` calls `unstakingVault.redeem(stETH, assetAmountUnlocked)` and then immediately attempts to transfer the full `assetAmountUnlocked` amount onward (split into fee + user portion). Because `LRTUnstakingVault.redeem()` internally calls `IERC20(stETH).safeTransfer(LRTWithdrawalManager, amount)`, and stETH's shares-based accounting causes the recipient to receive `amount - 1` or `amount - 2` wei, the contract ends up 1–2 wei short. The subsequent `_transferAsset` to the user then reverts, making `instantWithdrawal` for stETH intermittently non-functional.

---

### Finding Description

`LRTWithdrawalManager.instantWithdrawal()` executes the following sequence:

1. Computes `assetAmountUnlocked = getExpectedAssetAmount(stETH, rsETHUnstaked)` from the oracle.
2. Burns `rsETHUnstaked` rsETH from the caller.
3. Calls `unstakingVault.redeem(stETH, assetAmountUnlocked)`.
4. Computes `fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000` and `userAmount = assetAmountUnlocked - fee`.
5. Calls `_transferAsset(stETH, feeRecipient, fee)`.
6. Calls `_transferAsset(stETH, msg.sender, userAmount)`. [1](#0-0) 

Inside `LRTUnstakingVault.redeem()`, for non-ETH assets the vault executes:

```solidity
IERC20(asset).safeTransfer(msg.sender, amount);
``` [2](#0-1) 

stETH is a rebasing token that stores balances as shares internally. When `transfer(amount)` is called, it converts `amount` to shares via `getSharesByPooledEth(amount)` (which rounds **down**), then transfers those shares. The recipient's resulting balance — `getPooledEthByShares(shares)` — is typically `amount - 1` or `amount - 2` wei. The `safeTransfer` call succeeds (stETH returns `true`), but `LRTWithdrawalManager` receives less than `assetAmountUnlocked`.

After step 3, the contract holds `assetAmountUnlocked - 1` stETH. Steps 5 and 6 together attempt to transfer `fee + userAmount = assetAmountUnlocked` stETH. After step 5 succeeds, the contract holds `assetAmountUnlocked - 1 - fee` stETH, but step 6 requires `assetAmountUnlocked - fee`. The contract is exactly 1 wei short, causing `_transferAsset` to revert.

The same pattern exists in `unlockQueue`, which also calls `unstakingVault.redeem(asset, assetAmountUnlocked)` and then relies on the contract holding exactly that amount for subsequent user withdrawals: [3](#0-2) 

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

`instantWithdrawal` for stETH reverts intermittently (whenever the stETH rounding shortfall occurs), preventing users from completing instant withdrawals. The rsETH burn is part of the same transaction and reverts with it, so user rsETH is not lost — but the withdrawal path is broken. The `onlyInstantWithdrawalAllowed` modifier confirms this is a live, user-facing path. [4](#0-3) 

---

### Likelihood Explanation

stETH's 1–2 wei rounding on `transfer` is a well-documented, frequently occurring behavior caused by its shares-based accounting. It is not a hypothetical edge case — it occurs on virtually every stETH transfer where the amount does not map to an exact share count. stETH (`ST_ETH_TOKEN`) is a first-class supported asset in this protocol. [5](#0-4) 

---

### Recommendation

Use a balance-before / balance-after pattern to determine the actual amount received from `unstakingVault.redeem()`, and use that actual amount for all subsequent fee and user transfer calculations:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
unstakingVault.redeem(asset, assetAmountUnlocked);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 fee = (actualReceived * instantWithdrawalFee) / 10_000;
uint256 userAmount = actualReceived - fee;
```

This mirrors the fix recommended in the reference report (M-1) and is the standard pattern for integrating with rebasing or fee-on-transfer tokens.

---

### Proof of Concept

1. Manager enables instant withdrawal for stETH.
2. User calls `instantWithdrawal(stETH, rsETHUnstaked, "")`.
3. `assetAmountUnlocked = X` is computed from the oracle.
4. `unstakingVault.redeem(stETH, X)` → internally calls `IERC20(stETH).safeTransfer(LRTWithdrawalManager, X)`.
5. Due to stETH rounding, `LRTWithdrawalManager` receives `X - 1` stETH.
6. `fee = (X * instantWithdrawalFee) / 10_000`, `userAmount = X - fee`.
7. `_transferAsset(stETH, feeRecipient, fee)` succeeds; contract now holds `X - 1 - fee`.
8. `_transferAsset(stETH, msg.sender, X - fee)` reverts — contract is 1 wei short.
9. Entire transaction reverts. User cannot complete instant withdrawal. [6](#0-5) [2](#0-1)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L118-119)
```text
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ETHX_TOKEN)] = unlockedWithdrawalsCountETHx;
```

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
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
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTUnstakingVault.sol (L99-105)
```text
    function redeem(address asset, uint256 amount) external nonReentrant onlyLRTWithdrawalManager {
        if (asset == LRTConstants.ETH_TOKEN) {
            ILRTWithdrawalManager(msg.sender).receiveFromLRTUnstakingVault{ value: amount }();
        } else {
            IERC20(asset).safeTransfer(msg.sender, amount);
        }
    }
```
