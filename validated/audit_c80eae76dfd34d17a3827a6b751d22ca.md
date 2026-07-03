### Title
ETH Deposit Limit Check Excludes New Deposit Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies inconsistent logic for ETH versus ERC20 assets. For ETH the incoming deposit amount is never added to `totalAssetDeposits` before comparing against the configured cap, so the limit can be breached by exactly one deposit. For every ERC20 asset the new amount is correctly included in the comparison.

### Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` branches on the asset type:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount included
}
``` [1](#0-0) 

For ETH the check is a strict-greater-than on the **pre-deposit** total. When `totalAssetDeposits == depositLimit` the expression `totalAssetDeposits > depositLimit` evaluates to `false`, so `_beforeDeposit` does not revert and the deposit is accepted. After the call `totalAssetDeposits` exceeds the configured cap by the full deposit amount.

The companion view function `getAssetCurrentLimit` already returns `0` in this state (it uses `>` as well, but returns `depositLimit - totalAssetDeposits = 0`), so the public API signals "no capacity remaining" while the internal guard still admits one more ETH deposit. [2](#0-1) 

`_beforeDeposit` is the sole caller of this guard and is invoked by both `depositETH` and `depositAsset`: [3](#0-2) 

### Impact Explanation
**Low.** The deposit limit is a risk-management cap that bounds the protocol's exposure to EigenLayer slashing. Bypassing it for ETH allows one additional deposit of arbitrary size beyond the cap, minting rsETH in excess of the intended ceiling and staking more ETH in EigenLayer than governance intended. No funds are directly stolen or frozen, but the protocol fails to enforce its own stated deposit ceiling for ETH, which is the analog of the SpinLottery weight-distribution inconsistency: the limit is computed correctly in the view layer but not enforced consistently in the execution layer.

### Likelihood Explanation
**Medium.** The condition is reached whenever `totalAssetDeposits` for ETH equals the configured `depositLimitByAsset`. Any unprivileged depositor can trigger this by being the last depositor to fill the pool to exactly the cap, or by observing on-chain state and submitting a deposit at that moment. No special role or front-running is required.

### Recommendation
Include the incoming deposit amount in the ETH branch, matching the ERC20 logic:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Users deposit until `getTotalAssetDeposits(ETH_TOKEN) == 1000 ether`.
3. `getAssetCurrentLimit(ETH_TOKEN)` returns `0` — the public API signals the cap is full.
4. Attacker calls `depositETH{value: 500 ether}(0, "")`.
5. Inside `_checkIfDepositAmountExceedesCurrentLimit`: `1000 ether > 1000 ether` → `false` → no revert.
6. `_mintRsETH` mints rsETH for 500 ETH; `getTotalAssetDeposits(ETH_TOKEN)` is now `1500 ether`, 50 % above the cap.
7. The excess ETH will be forwarded to NodeDelegators and staked in EigenLayer beyond the intended risk ceiling.

### Citations

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
