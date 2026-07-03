The code confirms the claim. Let me verify the exact flow:

In `instantWithdrawal` ( [1](#0-0) ), `unstakingVault.redeem(asset, assetAmountUnlocked)` is called, and then `fee + userAmount = assetAmountUnlocked` is transferred out.

In `LRTUnstakingVault.redeem()` ( [2](#0-1) ), for non-ETH assets, `IERC20(asset).safeTransfer(msg.sender, amount)` is called with the exact `amount`.

stETH's shares-based accounting means the recipient receives `amount - 1` or `amount - 2` wei, making the subsequent outbound transfers short by 1–2 wei.

---

Audit Report

## Title
`instantWithdrawal` Reverts for stETH Due to stETH Transfer Rounding Shortfall — (`contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTWithdrawalManager.instantWithdrawal()` calls `unstakingVault.redeem(stETH, assetAmountUnlocked)`, which internally executes `IERC20(stETH).safeTransfer(LRTWithdrawalManager, assetAmountUnlocked)`. Due to stETH's shares-based accounting, the contract receives `assetAmountUnlocked - 1` or `assetAmountUnlocked - 2` wei. The function then attempts to transfer the full `assetAmountUnlocked` (as `fee + userAmount`) outward, causing the final `_transferAsset` to revert. This makes `instantWithdrawal` for stETH non-functional.

## Finding Description
In `instantWithdrawal` (lines 228–250 of `LRTWithdrawalManager.sol`):

1. `assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked)` is computed from the oracle.
2. rsETH is burned from the caller (`burnFrom` at line 229).
3. `unstakingVault.redeem(asset, assetAmountUnlocked)` is called (line 235).
4. Inside `LRTUnstakingVault.redeem()` (lines 99–105), for non-ETH assets: `IERC20(asset).safeTransfer(msg.sender, amount)` executes. stETH converts `amount` to shares via `getSharesByPooledEth(amount)` (rounds down), then transfers those shares. The `LRTWithdrawalManager` receives `getPooledEthByShares(shares)` = `assetAmountUnlocked - 1` (or `-2`) wei. The `safeTransfer` returns `true` and does not revert.
5. Back in `instantWithdrawal`: `fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000`, `userAmount = assetAmountUnlocked - fee` (lines 237–238). Both are computed from the original `assetAmountUnlocked`, not the actual received amount.
6. `_transferAsset(stETH, feeRecipient, fee)` (line 246) succeeds if `fee > 0`.
7. `_transferAsset(stETH, msg.sender, userAmount)` (line 250) reverts — the contract holds `assetAmountUnlocked - 1 - fee` but attempts to send `assetAmountUnlocked - fee`.

When `instantWithdrawalFee == 0`, `fee = 0` and `userAmount = assetAmountUnlocked`, so step 7 directly reverts with the 1-wei shortfall. No existing guard checks the actual received balance after `redeem`.

## Impact Explanation
**Medium — Temporary freezing of funds.** The entire transaction reverts, including the rsETH burn (so user rsETH is not lost). However, the `instantWithdrawal` path for stETH is rendered non-functional: users cannot complete instant withdrawals for stETH. The `onlyInstantWithdrawalAllowed` modifier confirms this is a live, user-facing withdrawal path.

## Likelihood Explanation
stETH's 1–2 wei rounding on `transfer` is a well-documented, deterministic behavior arising from its shares-based accounting. It occurs on virtually every stETH transfer where the amount does not correspond to an exact share count — which is the common case. stETH (`ST_ETH_TOKEN`) is a first-class supported asset in this protocol (referenced in `initialize2` at lines 118–119). Any user calling `instantWithdrawal` for stETH after the manager enables it will trigger this revert with near-certainty.

## Recommendation
Use a balance-before/balance-after pattern to determine the actual amount received from `unstakingVault.redeem()`, and base all subsequent fee and user transfer calculations on the actual received amount:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
unstakingVault.redeem(asset, assetAmountUnlocked);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 fee = (actualReceived * instantWithdrawalFee) / 10_000;
uint256 userAmount = actualReceived - fee;
```

This is the standard pattern for integrating with rebasing or fee-on-transfer tokens and eliminates the dependency on the oracle-computed `assetAmountUnlocked` for outbound transfer amounts.

## Proof of Concept
1. Manager calls `setInstantWithdrawalEnabled(stETH, true)`.
2. User calls `instantWithdrawal(stETH, rsETHUnstaked, "")`.
3. `assetAmountUnlocked = X` is computed from the oracle (line 228).
4. rsETH is burned from the user (line 229).
5. `unstakingVault.redeem(stETH, X)` → `IERC20(stETH).safeTransfer(LRTWithdrawalManager, X)` executes (vault line 103).
6. Due to stETH rounding, `LRTWithdrawalManager` receives `X - 1` stETH (safeTransfer returns `true`).
7. `fee = (X * instantWithdrawalFee) / 10_000`, `userAmount = X - fee` (lines 237–238).
8. If `fee > 0`: `_transferAsset(stETH, feeRecipient, fee)` succeeds; contract holds `X - 1 - fee`.
9. `_transferAsset(stETH, msg.sender, X - fee)` reverts — contract is 1 wei short.
10. Entire transaction reverts. User cannot complete instant withdrawal for stETH.

Foundry fork test: fork mainnet, deploy/configure with stETH instant withdrawal enabled, call `instantWithdrawal` with any non-trivial `rsETHUnstaked` amount, and observe the revert at the final `safeTransfer` in `_transferAsset`.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L235-250)
```text
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
