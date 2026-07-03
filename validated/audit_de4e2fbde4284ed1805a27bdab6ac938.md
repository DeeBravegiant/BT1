Audit Report

## Title
Zero `userAmount` Transfer Without Guard in `instantWithdrawal` Burns User rsETH for Nothing - (File: contracts/LRTWithdrawalManager.sol)

## Summary
In `LRTWithdrawalManager.instantWithdrawal`, when `rsETHUnstaked * rsETHPrice < assetPrice`, integer division in `getExpectedAssetAmount` truncates `assetAmountUnlocked` to zero. The function then burns the caller's rsETH irreversibly and transfers zero assets back, with no revert. The maximum exploitable loss is 1 wei rsETH per call, but the contract fails to deliver its promised return.

## Finding Description
The guard at line 224 only rejects `rsETHUnstaked == 0` or values below `minRsEthAmountToWithdraw[asset]`, whose default mapping value is `0`:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

`getExpectedAssetAmount` computes:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

With `rsETHUnstaked = 1 wei` and `assetPrice > rsETHPrice` (e.g., rsETH depegged below an LST), this yields `assetAmountUnlocked = 0`. The burn executes unconditionally before any check on the computed amount:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked); // = 0
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);     // burned
```

The availability check `assetAmountUnlocked > getAssetsAvailableForInstantWithdrawal(asset)` passes trivially when `assetAmountUnlocked = 0`. Fee and user amount are both zero. `_transferAsset` with `amount = 0` for ETH executes `call{ value: 0 }("")` which succeeds silently; for ERC20, `safeTransfer(to, 0)` also succeeds silently. No revert occurs; the rsETH is permanently destroyed.

## Impact Explanation
A user calling `instantWithdrawal` with `rsETHUnstaked = 1 wei` under conditions where `rsETHPrice < assetPrice` has their rsETH burned and receives zero assets. The practical maximum loss per call is 1 wei rsETH (since `rsETHUnstaked = 2 wei` would yield `assetAmountUnlocked >= 1`), making the monetary loss dust-level. However, the contract demonstrably fails to deliver its promised return — burning rsETH is supposed to redeem underlying assets — mapping to **Low: Contract fails to deliver promised returns**.

## Likelihood Explanation
For ETH as the withdrawal asset, `rsETHPrice` is normally ≥ `assetPrice` (rsETH accumulates staking rewards), so the condition is unlikely under normal operation. For LSTs (stETH, ETHx) priced near or above rsETH's peg, `assetPrice > rsETHPrice` is reachable during stress or mild depeg events. Any unprivileged user can call `instantWithdrawal` with `rsETHUnstaked = 1 wei` when `minRsEthAmountToWithdraw[asset] = 0` (the default). Likelihood is **Low**.

## Recommendation
Add a zero-value guard on `assetAmountUnlocked` before burning rsETH, to prevent any state changes when the computed payout is zero:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
if (assetAmountUnlocked == 0) revert InvalidAmountToWithdraw();
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

Additionally, mirror the existing fee guard on the user transfer:

```solidity
if (userAmount == 0) revert InvalidAmountToWithdraw();
_transferAsset(asset, msg.sender, userAmount);
```

## Proof of Concept
1. Deploy with `minRsEthAmountToWithdraw[ETH] = 0` (default) and `instantWithdrawalFee = 0`.
2. Set oracle: `rsETHPrice = 1e18`, `getAssetPrice(ETH) = 2e18` (rsETH depegged to 0.5 ETH).
3. Call `instantWithdrawal(ETH, 1, "")` with `rsETHUnstaked = 1 wei`.
4. `assetAmountUnlocked = 1 * 1e18 / 2e18 = 0`.
5. `burnFrom(msg.sender, 1)` executes — 1 wei rsETH permanently destroyed.
6. Availability check: `0 > available` → false, no revert.
7. `unstakingVault.redeem(ETH, 0)` — no-op.
8. `fee = 0`, `userAmount = 0`.
9. `call{ value: 0 }(msg.sender)` — succeeds silently.
10. User lost 1 wei rsETH, received 0 ETH. Transaction completes without revert. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L224-229)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L245-250)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L876-879)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
```
