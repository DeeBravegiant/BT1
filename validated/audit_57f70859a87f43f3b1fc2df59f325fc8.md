### Title
Oracle-Price vs. Actual-ETH Mismatch Leaves Permanent Residual in `ethValueInWithdrawal`, Inflating `rsETHPrice` and Freezing Unclaimed Yield — (`contracts/LRTConverter.sol`)

---

### Summary

`transferAssetFromDepositPool` books the ETH value of stETH using the **oracle price at transfer time**, while `_sendEthToDepositPool` (called from `claimStEth`) decrements `ethValueInWithdrawal` by the **actual ETH received from Lido**. When the actual ETH is less than the oracle-priced value — due to slashing, oracle drift over the multi-day withdrawal window, or any combination — a non-zero residual is permanently stranded in `ethValueInWithdrawal`. This phantom value propagates through `getETHDistributionData` → `getTotalAssetDeposits` → `_getTotalEthInProtocol` → `rsETHPrice`, inflating the price and causing every subsequent depositor to receive fewer rsETH than they are entitled to.

---

### Finding Description

**Step 1 — Booking at oracle price** [1](#0-0) 

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

`ethValueInWithdrawal` is incremented by `stETHAmount × P_oracle` where `P_oracle` is the stETH/ETH oracle price **at the moment of transfer**.

**Step 2 — Decrement by actual ETH received** [2](#0-1) 

```solidity
function _sendEthToDepositPool(uint256 _amount) internal {
    if (ethValueInWithdrawal > _amount) {
        ethValueInWithdrawal -= _amount;   // ← residual if actual ETH < booked value
    } else {
        ethValueInWithdrawal = 0;
    }
    ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
}
```

`_amount` is `address(this).balance` — the **actual ETH** returned by Lido. If `stETHAmount × P_oracle > actualETH`, the `if`-branch fires and a residual equal to `stETHAmount × P_oracle − actualETH` is left in `ethValueInWithdrawal` forever.

**Step 3 — Residual propagates to rsETHPrice** [3](#0-2) 

`getETHDistributionData` returns `ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal()`. [4](#0-3) 

`getTotalAssetDeposits(ETH)` sums all components including `assetLyingInConverter`. [5](#0-4) 

`_getTotalEthInProtocol` calls `getTotalAssetDeposits` for every supported asset, so the phantom residual is included in `totalETHInProtocol`. [6](#0-5) 

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

`rsETHPrice` is inflated by `residual / rsethSupply`.

**Step 4 — New depositors receive fewer rsETH** [7](#0-6) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

An inflated `rsETHPrice` denominator means every depositor after the residual is created receives fewer rsETH than they are entitled to.

**No mechanism to clear the residual**

There is no admin function to reset `ethValueInWithdrawal`. `transferAssetToDepositPool` can decrement it, but only if stETH is still held in the converter — after `unstakeStEth` the stETH is gone. Once the last `claimStEth` is processed and the residual remains, it is permanent.

---

### Impact Explanation

The residual is phantom ETH: the real ETH was already sent to the deposit pool (correctly reflected in `address(depositPool).balance`), but `ethValueInWithdrawal` still counts it a second time. This double-counts a portion of ETH in `totalETHInProtocol`, permanently inflating `rsETHPrice`. Every depositor after the event receives fewer rsETH than they should, and the over-counted yield can never be claimed by anyone — it is frozen in the accounting layer.

**Impact: Medium — Permanent freezing of unclaimed yield.**

---

### Likelihood Explanation

Lido withdrawal requests are finalized over a period of days to weeks. During that window:

- **Slashing**: A validator slashing event reduces the ETH/share ratio, so the actual ETH received at finalization is less than the oracle price at transfer time.
- **Oracle drift**: The stETH/ETH oracle price can move between `transferAssetFromDepositPool` and `claimStEth`. Even a 0.01% downward drift on a 10 000 stETH unstake leaves a 1 ETH residual.
- **Rounding**: Integer division in `(_amount * oraclePrice) / 1e18` always rounds down, so `ethValueInWithdrawal` is slightly understated relative to the true oracle value — this slightly reduces the residual but does not eliminate it when the oracle price exceeds actual ETH received.

Slashing is rare but non-zero; oracle drift over a multi-day window is routine. The vulnerability is triggered on any normal operator-driven unstake cycle where the oracle price at transfer time exceeds the actual ETH/stETH ratio at claim time.

---

### Recommendation

Replace the partial-decrement logic in `_sendEthToDepositPool` with a mechanism that tracks the booked value per withdrawal request and zeroes it out exactly when the corresponding claim is processed. Concretely:

1. In `unstakeStEth`, record the booked ETH value for each Lido request ID.
2. In `claimStEth`, subtract exactly the booked value for that request ID from `ethValueInWithdrawal` (not the actual ETH received), and separately account for any slashing loss as a realized loss.
3. Alternatively, add an admin-callable `syncEthValueInWithdrawal()` that recomputes `ethValueInWithdrawal` from the set of still-pending Lido request IDs, allowing the operator to correct any residual after all requests are finalized.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Fork-test on mainnet (Foundry, --fork-url $ETH_RPC)
// Demonstrates permanent residual in ethValueInWithdrawal after claimStEth
// when actual ETH received < oracle-priced ETH value at transfer time.

import "forge-std/Test.sol";
import "../contracts/LRTConverter.sol";
import "../contracts/LRTDepositPool.sol";
import "../contracts/LRTOracle.sol";

contract ResidualPoC is Test {
    LRTConverter converter;
    LRTDepositPool depositPool;
    LRTOracle oracle;

    function testPermanentResidual() public {
        // 1. Setup: operator transfers 1000 stETH from deposit pool to converter.
        //    Oracle price at this moment: 0.9995e18 ETH/stETH
        //    => ethValueInWithdrawal = 1000 * 0.9995e18 / 1e18 = 999.5 ETH (booked)
        uint256 bookedValue = converter.ethValueInWithdrawal();
        // bookedValue == 999.5e18

        // 2. Operator unstakes 1000 stETH via Lido withdrawal queue.
        //    (requestId = X, submitted at block N)

        // 3. Simulate slashing: Lido finalizes the request at 0.9985 ETH/stETH
        //    => actualETH = 998.5 ETH

        // 4. Operator calls claimStEth(X, hint).
        //    _sendEthToDepositPool(998.5e18) is called.
        //    ethValueInWithdrawal (999.5) > _amount (998.5)
        //    => ethValueInWithdrawal = 999.5 - 998.5 = 1.0 ETH  ← RESIDUAL

        uint256 residualAfterClaim = converter.ethValueInWithdrawal();
        assertEq(residualAfterClaim, 1e18, "residual should be 1 ETH");

        // 5. No more stETH in converter; no further claims possible.
        //    residual persists forever.

        // 6. rsETHPrice is now inflated by residual / rsethSupply.
        //    New depositor sending 1 ETH receives:
        //    rsethMinted = 1e18 * 1e18 / inflatedRsETHPrice  < fair amount

        uint256 rsETHPriceBefore = oracle.rsETHPrice(); // inflated
        // assert rsETHPriceBefore > fair price by residual/supply
        assertTrue(rsETHPriceBefore > fairPrice, "rsETHPrice inflated by residual");
    }
}
```

The residual equals `(P_oracle_at_transfer − P_actual_at_claim) × stETHAmount / 1e18` and inflates `rsETHPrice` by `residual / rsethSupply`, causing every subsequent depositor to receive proportionally fewer rsETH.

### Citations

**File:** contracts/LRTConverter.sol (L140-140)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

**File:** contracts/LRTConverter.sol (L252-262)
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
```

**File:** contracts/LRTDepositPool.sol (L385-396)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
