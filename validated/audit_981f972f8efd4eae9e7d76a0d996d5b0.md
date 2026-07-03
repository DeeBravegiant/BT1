### Title
Zero `userAmount` Transfer Without Guard in `instantWithdrawal` Burns User rsETH for Nothing - (File: contracts/LRTWithdrawalManager.sol)

### Summary
In `LRTWithdrawalManager.instantWithdrawal`, the remaining user amount (`userAmount = assetAmountUnlocked - fee`) is transferred to the caller without a zero-value guard. If `assetAmountUnlocked` rounds to zero due to integer division (small `rsETHUnstaked` relative to the price ratio), the user's rsETH is permanently burned while they receive zero assets in return.

### Finding Description
The `instantWithdrawal` function computes the fee and the user's remaining amount, then conditionally transfers the fee only if non-zero, but unconditionally transfers `userAmount` to the user:

```solidity
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
// ...
if (fee > 0) {
    _transferAsset(asset, feeRecipient, fee);   // guarded
}
_transferAsset(asset, msg.sender, userAmount);  // NO zero-check
```

`assetAmountUnlocked` is computed as:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

If `rsETHUnstaked * rsETHPrice < assetPrice` (e.g., `rsETHUnstaked = 1 wei` when `assetPrice > rsETHPrice`), integer division truncates `assetAmountUnlocked` to zero. Since `instantWithdrawalFee` is capped at 1000 BPS (10%), `userAmount` can only be zero when `assetAmountUnlocked` itself is zero. There is no guard preventing this path:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked); // burned first
// ...
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable(); // 0 <= available, passes
}
unstakingVault.redeem(asset, assetAmountUnlocked); // redeem(asset, 0) — no-op
// ...
_transferAsset(asset, msg.sender, userAmount); // transfers 0
```

The rsETH burn is irreversible; the user permanently loses their rsETH tokens while receiving nothing.

### Impact Explanation
A user calling `instantWithdrawal` with a small `rsETHUnstaked` value that causes `assetAmountUnlocked` to round to zero will have their rsETH permanently burned with zero assets returned. rsETH represents a proportional claim on the protocol's restaked assets; burning it for nothing constitutes a permanent loss of user funds. This maps to **Low** ("contract fails to deliver promised returns") at minimum, escalating toward permanent fund loss if the burned rsETH amount is non-trivial.

### Likelihood Explanation
The default value of `minRsEthAmountToWithdraw[asset]` is zero, meaning the only input guard is `rsETHUnstaked == 0`. Any user can pass `rsETHUnstaked = 1 wei`. For the rounding to produce zero, `rsETHPrice < assetPrice` must hold (e.g., during a depeg or if a supported LST appreciates above rsETH's peg). For the currently supported assets (ETH, stETH, ETHx — all priced near 1 ETH), this condition is unlikely under normal market conditions but becomes reachable during stress or depeg events. Likelihood is **Low**.

### Recommendation
Add an explicit zero-value guard before transferring `userAmount` to the user, mirroring the existing guard on the fee transfer:

```solidity
if (userAmount == 0) revert InvalidAmountToWithdraw();
_transferAsset(asset, msg.sender, userAmount);
```

Additionally, consider adding a guard that `assetAmountUnlocked > 0` immediately after computing it, before burning the user's rsETH, to prevent any state changes when the computed payout is zero.

### Proof of Concept
1. Protocol is deployed with `minRsEthAmountToWithdraw[ETH] = 0` (default) and `instantWithdrawalFee = 0`.
2. rsETH oracle price = 1e18; ETH asset price = 2e18 (rsETH has depegged to 0.5 ETH).
3. Attacker calls `instantWithdrawal(ETH, 1, "")` with `rsETHUnstaked = 1 wei`.
4. `assetAmountUnlocked = 1 * 1e18 / 2e18 = 0` (integer division).
5. `IRSETH.burnFrom(msg.sender, 1)` executes — 1 wei rsETH is permanently destroyed.
6. `unstakingVault.redeem(ETH, 0)` — no-op.
7. `fee = 0`, `userAmount = 0`.
8. `_transferAsset(ETH, msg.sender, 0)` — `call{ value: 0 }("")` succeeds silently.
9. User has lost 1 wei rsETH and received 0 ETH. No revert occurs. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L228-235)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L237-250)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L876-883)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
            IERC20(asset).safeTransfer(to, amount);
        }
    }
```
