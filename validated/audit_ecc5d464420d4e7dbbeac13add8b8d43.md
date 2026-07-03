### Title
`ethValueInWithdrawal` Not Reduced on Lido Unstaking Loss, Inflating rsETH Price — (File: `contracts/LRTConverter.sol`)

---

### Summary

`LRTConverter` maintains an internal accounting variable `ethValueInWithdrawal` that tracks the ETH-denominated value of LST assets (e.g., stETH) sent into the Lido withdrawal queue. This variable is added to the protocol's total ETH calculation and directly drives the rsETH price. When the actual ETH received from Lido upon claim is less than the value recorded at transfer time (due to stETH price movement or validator slashing), the residual phantom value in `ethValueInWithdrawal` permanently inflates the reported total ETH in the protocol, overstating the rsETH price.

---

### Finding Description

**Step 1 — Asset transfer records ETH value at oracle price:**

When an operator calls `transferAssetFromDepositPool`, the ETH value of the transferred stETH is recorded using the oracle price at that moment:

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
``` [1](#0-0) 

**Step 2 — Unstaking sends stETH to Lido; `ethValueInWithdrawal` is not touched:**

`unstakeStEth` sends the stETH to Lido's withdrawal queue but makes no adjustment to `ethValueInWithdrawal`. The variable continues to reflect the oracle price at transfer time. [2](#0-1) 

**Step 3 — Claim reduces `ethValueInWithdrawal` by actual ETH received, not by the originally recorded value:**

```solidity
function _sendEthToDepositPool(uint256 _amount) internal {
    if (ethValueInWithdrawal > _amount) {
        ethValueInWithdrawal -= _amount;
    } else {
        ethValueInWithdrawal = 0;
    }
    ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
}
``` [3](#0-2) 

If `_amount` (actual ETH from Lido) < the originally recorded ETH value, the difference remains in `ethValueInWithdrawal` as a phantom value with no corresponding real asset.

**Step 4 — `ethValueInWithdrawal` feeds directly into rsETH price:**

`getETHDistributionData` reads `ethValueInWithdrawal` as `ethLyingInConverter`: [4](#0-3) 

This feeds into `getTotalAssetDeposits`: [5](#0-4) 

Which feeds into `_getTotalEthInProtocol` in `LRTOracle`: [6](#0-5) 

Which determines the rsETH price: [7](#0-6) 

---

### Impact Explanation

The phantom residual in `ethValueInWithdrawal` inflates `totalETHInProtocol`, causing `rsETHPrice` to be overstated. Consequences:

1. **New depositors** receive fewer rsETH tokens than they should (they pay the inflated price).
2. **Existing rsETH holders** appear to hold more value than actually exists in the protocol.
3. **Withdrawers** who redeem rsETH at the inflated price receive more underlying assets than the protocol can cover, eventually leaving the last withdrawers unable to complete their withdrawals — a **temporary or permanent freeze of funds**.

This matches the "Contract fails to deliver promised returns" (Low) to "Temporary freezing of funds" (Medium) impact range, depending on the magnitude of the loss.

---

### Likelihood Explanation

Two realistic triggers exist:

- **stETH oracle price drift:** `ethValueInWithdrawal` is set at the oracle price at the time of `transferAssetFromDepositPool`. If the stETH/ETH rate falls between transfer and claim (e.g., from 1.05 to 1.00), the actual ETH received from Lido is less than the recorded value, leaving a residual.
- **Lido validator slashing:** A slashing event reduces the ETH returned by Lido below the stETH face value, directly creating the phantom residual.

Both scenarios are low-probability but non-negligible over the protocol's lifetime, especially as the stETH position grows.

---

### Recommendation

When `claimStEth` is called, reset `ethValueInWithdrawal` to zero (or to the exact pre-claim recorded value for that request) rather than subtracting the actual ETH received. Alternatively, track per-request ETH values and clear them precisely on claim, so no phantom residual can accumulate regardless of the actual ETH returned.

---

### Proof of Concept

1. Operator calls `transferAssetFromDepositPool(stETH, 1000e18)` when oracle price = 1.05 ETH/stETH → `ethValueInWithdrawal = 1050e18`.
2. Operator calls `unstakeStEth(1000e18)` → 1000 stETH enters Lido withdrawal queue; `ethValueInWithdrawal` remains `1050e18`.
3. stETH/ETH rate drops to 1.00 (or slashing occurs); Lido returns 1000 ETH.
4. Operator calls `claimStEth(requestId, hint)` → `_sendEthToDepositPool(1000e18)` → `ethValueInWithdrawal = 1050e18 - 1000e18 = 50e18`.
5. `50e18` phantom ETH permanently inflates `totalETHInProtocol` → rsETH price is overstated → protocol is undercollateralized by 50 ETH.
6. Any rsETH holder who redeems at the inflated price extracts more real ETH than exists, leaving later withdrawers unable to claim their funds.

### Citations

**File:** contracts/LRTConverter.sol (L140-142)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
```

**File:** contracts/LRTConverter.sol (L170-177)
```text
    function unstakeStEth(uint256 amountToUnstake)
        external
        nonReentrant
        onlyLRTOperator
        withinUnstakeLimits(amountToUnstake)
    {
        _unstakeStEth(amountToUnstake);
    }
```

**File:** contracts/LRTConverter.sol (L252-263)
```text
    function _sendEthToDepositPool(uint256 _amount) internal {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
        // Send eth to deposit pool
        ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
        emit EthTransferred(lrtDepositPoolAddress, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L394-396)
```text
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
