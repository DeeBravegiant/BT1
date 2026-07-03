### Title
`getExpectedAssetAmount` Returns Pre-Fee Amount, Overstating Instant Withdrawal Proceeds - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`getExpectedAssetAmount` is a public view function that callers use to preview how much underlying asset they will receive when burning rsETH. For the `instantWithdrawal` path, the function's return value is higher than what the user actually receives, because the `instantWithdrawalFee` is applied inside `instantWithdrawal` after `getExpectedAssetAmount` is called, but is never reflected in the view function's output.

### Finding Description

`getExpectedAssetAmount` computes the gross asset amount from the rsETH/asset oracle ratio, with no awareness of `instantWithdrawalFee`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

Inside `instantWithdrawal`, the fee is deducted from that gross amount after the fact:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
...
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
...
_transferAsset(asset, msg.sender, userAmount);
emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
```

A user who queries `getExpectedAssetAmount` before calling `instantWithdrawal` receives a value that is `instantWithdrawalFee` basis points higher than what they will actually receive. The emitted `AssetWithdrawalFinalized` event records `userAmount` (post-fee), confirming the discrepancy between the view function and the actual transfer.

This is structurally identical to the referenced report: a public view function that is supposed to tell the caller how much they will receive returns a value that does not account for the fee that is applied during execution.

### Impact Explanation

Any off-chain integration, UI, or smart contract that calls `getExpectedAssetAmount` to preview an instant withdrawal will display or act on an inflated figure. The user burns the full `rsETHUnstaked` but receives `userAmount < getExpectedAssetAmount(asset, rsETHUnstaked)`. The contract fails to deliver the amount it advertises through its public interface. No funds are lost from the protocol, but the user receives less than the promised return.

**Severity: Low** — Contract fails to deliver promised returns, but doesn't lose value.

### Likelihood Explanation

`getExpectedAssetAmount` is part of the `ILRTWithdrawalManager` interface and is explicitly exposed for external consumption. Any user or integrator previewing an instant withdrawal before executing it will encounter the discrepancy. The `instantWithdrawalFee` can be up to 1000 bps (10%), making the gap material.

### Recommendation

Add a dedicated view function for instant withdrawal previews that deducts the fee:

```solidity
function getExpectedInstantWithdrawalAmount(address asset, uint256 rsETHAmount)
    external view returns (uint256 userAmount, uint256 fee)
{
    uint256 gross = getExpectedAssetAmount(asset, rsETHAmount);
    fee = (gross * instantWithdrawalFee) / 10_000;
    userAmount = gross - fee;
}
```

Alternatively, document clearly that `getExpectedAssetAmount` returns the gross (pre-fee) amount and should not be used to estimate instant withdrawal proceeds.

### Proof of Concept

1. `instantWithdrawalFee` is set to 500 bps (5%).
2. User calls `getExpectedAssetAmount(ETH, 1e18 rsETH)` → returns `1.05 ETH` (hypothetical rate).
3. User calls `instantWithdrawal(ETH, 1e18, "")`.
4. Inside `instantWithdrawal`: `assetAmountUnlocked = 1.05 ETH`, `fee = 0.0525 ETH`, `userAmount = 0.9975 ETH`.
5. User receives `0.9975 ETH`, not `1.05 ETH` as advertised by `getExpectedAssetAmount`.

Root cause lines: [1](#0-0) 

Fee deduction applied post-hoc in `instantWithdrawal`, not reflected in the view: [2](#0-1)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L228-252)
```text
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
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
