Audit Report

## Title
`rsETHPrice` and `highestRsethPrice` Unconditionally Reset to `1 ether` When `rsethSupply == 0`, Enabling First-Depositor Theft of Orphaned Protocol Rewards - (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle._updateRsETHPrice()` unconditionally overwrites both `rsETHPrice` and `highestRsethPrice` to `1 ether` whenever `rsethSupply == 0`, discarding all previously accumulated price history. Because `updateRSETHPrice()` is a public, permissionless function and `_getTotalEthInProtocol()` can return non-zero values even when rsETH supply is zero (due to EigenLayer staking rewards accrued in `NodeDelegator` contracts), the first depositor after the reset captures all orphaned yield at the artificially deflated price.

## Finding Description
In `LRTOracle._updateRsETHPrice()`, lines 218–222:

```solidity
if (rsethSupply == 0) {
    rsETHPrice = 1 ether;
    highestRsethPrice = 1 ether;
    return;
}
``` [1](#0-0) 

Both state variables are overwritten with no regard for the previous price. The public entry point is:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

`rsethSupply` can legitimately reach zero through the normal withdrawal lifecycle: `RSETH.burnFrom()` is callable by any address holding `BURNER_ROLE` (which `LRTWithdrawalManager` holds), and `instantWithdrawal` is a supported path gated only by `isInstantWithdrawalEnabled[asset]`. [3](#0-2) [4](#0-3) 

At the moment of reset, `_getTotalEthInProtocol()` may still be non-zero because it sums `assetStakedInEigenLayer + assetUnstakingFromEigenLayer` across all `NodeDelegator` contracts via `getTotalAssetDeposits()`: [5](#0-4) [6](#0-5) 

EigenLayer beacon-chain staking rewards accrue continuously inside `NodeDelegator` contracts (counted via `getEffectivePodShares()`) and are only moved to the deposit pool on operator action. These rewards are included in `getTotalAssetDeposits` but are not automatically distributed when rsETH supply drops to zero.

After the reset, `getRsETHAmountToMint` uses the deflated `rsETHPrice = 1 ether`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [7](#0-6) 

A new depositor therefore receives rsETH priced as if the protocol holds zero accumulated yield. On the next `updateRSETHPrice()` call, the price jumps to reflect the residual assets, and the new depositor captures the entire orphaned reward balance.

A secondary impact is that `highestRsethPrice` is also reset to `1 ether`. This value governs both the upside price-increase guard and the downside auto-pause: [8](#0-7) [9](#0-8) 

Resetting `highestRsethPrice` from, say, `1.05 ether` to `1 ether` means a price drop to `0.95 ether` (a ~9.5% drop from the true peak) no longer triggers the auto-pause, permanently weakening the downside protection for all subsequent depositors.

## Impact Explanation
**High — Theft of unclaimed yield.** Orphaned EigenLayer staking rewards that have accrued inside `NodeDelegator` contracts but have not yet been swept to the deposit pool are captured in full by the first depositor after the reset. The magnitude equals all rewards accumulated since the last operator sweep, which in a large deployment can be substantial. Additionally, the `highestRsethPrice` reset permanently weakens the price-deviation circuit-breaker for the new deposit epoch.

## Likelihood Explanation
`rsethSupply` reaching zero is a realistic edge case occurring naturally during a protocol migration, a coordinated mass-exit, or when `instantWithdrawal` is enabled and all holders exit. The public `updateRSETHPrice()` function means no privileged access is required to trigger the reset — any EOA can call it the moment supply hits zero. `pricePercentageLimit` is initialized to `0` by default (no explicit initialization in the contract), so the price-increase guard is disabled unless the admin has explicitly set it, making the single-block capture path viable. [10](#0-9) 

## Recommendation
Preserve the last known price when `rsethSupply` drops to zero rather than resetting it:

```solidity
if (rsethSupply == 0) {
    // Do not reset rsETHPrice or highestRsethPrice.
    // The stored price remains valid for the next depositor.
    return;
}
```

If a deliberate reset to `1 ether` is desired (e.g., after a full protocol migration), it should be gated behind an explicit admin action rather than triggered automatically by a public function.

## Proof of Concept
1. Protocol has been running; `rsETHPrice = 1.05 ether`, `highestRsethPrice = 1.05 ether`. EigenLayer rewards of `R` ETH have accrued inside `NodeDelegator` contracts and are counted by `getEffectivePodShares()`.
2. All rsETH holders exit via `instantWithdrawal` (or the standard queue). `rsethSupply → 0`. The `R` ETH rewards remain in the `NodeDelegator` and are still returned by `getTotalAssetDeposits`.
3. Attacker calls `updateRSETHPrice()`. Because `rsethSupply == 0`, `rsETHPrice = 1 ether` and `highestRsethPrice = 1 ether`.
4. Attacker calls `LRTDepositPool.depositETH{value: 1 ether}(0, "")`. `getRsETHAmountToMint` computes `(1e18 * 1e18) / 1e18 = 1 rsETH`. Attacker receives `1 rsETH`.
5. Attacker calls `updateRSETHPrice()` again. Now `rsethSupply = 1`, `totalETHInProtocol = 1 ETH (deposit) + R ETH (rewards)`. `rsETHPrice = (1 + R) * 1e18`.
6. Attacker initiates withdrawal and receives `(1 + R)` ETH for `1 rsETH` deposited, stealing `R` ETH of orphaned staking rewards.

### Citations

**File:** contracts/LRTOracle.sol (L29-30)
```text
    uint256 public pricePercentageLimit;
    uint256 public highestRsethPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L252-257)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L270-274)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L331-348)
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
```

**File:** contracts/RSETH.sol (L245-248)
```text
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L55-56)
```text
    mapping(address asset => bool) public isInstantWithdrawalEnabled;
    uint256 public instantWithdrawalFee; // Fee in basis points (1 = 0.01%)
```

**File:** contracts/LRTDepositPool.sol (L394-396)
```text
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
