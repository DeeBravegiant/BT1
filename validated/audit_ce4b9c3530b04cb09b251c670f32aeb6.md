### Title
`instantWithdrawal` Fee Bypassed via Rounding to Zero for Dust Amounts - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager.instantWithdrawal` computes the protocol fee using integer division that rounds down to zero when the asset amount is below the threshold `10_000 / instantWithdrawalFee`. An unprivileged rsETH holder can call `instantWithdrawal` repeatedly with dust-sized inputs to redeem assets without paying any instant-withdrawal fee, depriving the fee recipient of protocol revenue.

### Finding Description

In `instantWithdrawal`, the fee is computed as:

```solidity
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
``` [1](#0-0) 

When `assetAmountUnlocked * instantWithdrawalFee < 10_000`, Solidity integer division truncates the result to `0`. The subsequent `if (fee > 0)` guard then skips the fee transfer entirely, and the user receives the full `assetAmountUnlocked` with no fee deducted. [2](#0-1) 

The minimum-amount guard only rejects `rsETHUnstaked == 0` when `minRsEthAmountToWithdraw[asset]` is at its default value of zero (uninitialized mapping), so any non-zero rsETH amount passes:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [3](#0-2) 

`assetAmountUnlocked` is derived from `rsETHUnstaked` via oracle prices:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

Since rsETH and ETH/LSTs are all 18-decimal tokens with prices near parity, `assetAmountUnlocked ≈ rsETHUnstaked`. The bypass threshold is therefore `rsETHUnstaked < 10_000 / instantWithdrawalFee` wei. At the minimum fee of 1 bps (`instantWithdrawalFee = 1`), any input below 9 999 wei produces `fee = 0`.

The same rounding pattern is present across all RSETHPool `deposit` variants:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
``` [5](#0-4) [6](#0-5) 

### Impact Explanation

The protocol fails to collect the instant-withdrawal fee for each dust-sized call. The fee avoided per call is at most `floor((9999 × instantWithdrawalFee) / 10_000) = 0` wei — i.e., less than one wei per transaction. No user principal is at risk and no funds are frozen. The impact is limited to loss of fee revenue on individually negligible amounts.

**Impact: Low** — Contract fails to deliver promised returns (fee collection) but does not lose value.

### Likelihood Explanation

The attack requires no special role or privilege — any rsETH holder can call `instantWithdrawal`. However, because all assets involved have 18 decimals, the per-call fee avoided is sub-wei, making the attack economically irrational on mainnet (gas cost far exceeds benefit). On low-cost L2s the economics improve marginally but remain impractical at scale. Likelihood is low.

### Recommendation

Replace the subtraction-based fee calculation with a muldiv over `(BASIS_POINTS - fee)` so that the fee rounds up rather than down, matching the fix recommended in M-27:

```solidity
// Before (rounds fee down, can be 0):
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;

// After (rounds userAmount down, fee is never 0 unless instantWithdrawalFee == 0):
uint256 userAmount = assetAmountUnlocked * (10_000 - instantWithdrawalFee) / 10_000;
uint256 fee = assetAmountUnlocked - userAmount;
```

Apply the same fix to `viewSwapRsETHAmountAndFee` in all RSETHPool variants.

Additionally, set a non-zero `minRsEthAmountToWithdraw` for each supported asset to enforce a meaningful floor on withdrawal size.

### Proof of Concept

Assume `instantWithdrawalFee = 1` (0.01%), `minRsEthAmountToWithdraw[ETH] = 0` (default), rsETH price ≈ ETH price.

1. Attacker holds rsETH and calls `instantWithdrawal(ETH, 9_999, "")`.
2. `assetAmountUnlocked = 9_999 * rsETHPrice / assetPrice ≈ 9_999 wei`.
3. `fee = (9_999 * 1) / 10_000 = 0` (integer truncation).
4. `userAmount = 9_999 - 0 = 9_999 wei` — full amount returned, zero fee collected.
5. Repeat in a loop; each iteration avoids a fee that would have been `< 1 wei`. [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L224-226)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
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

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/pools/RSETHPool.sol (L312-313)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L278-279)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```
