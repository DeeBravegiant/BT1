### Title
ETH Deposit Limit Check Missing `amount` in Comparison, Allowing Deposits Beyond Configured Cap - (File: contracts/LRTDepositPool.sol)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit()` in `LRTDepositPool` applies an asymmetric check: for LST assets it correctly tests `totalAssetDeposits + amount > depositLimit`, but for ETH it omits `amount` and only tests `totalAssetDeposits > depositLimit`. This mirrors the external report's pattern exactly — a deposit-cap calculation that silently omits one component, making the effective cap appear larger than intended.

### Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

For ETH the function returns `true` (i.e., "limit exceeded") only when the **current** total already exceeds the cap. It never tests whether the **incoming** deposit would push the total over the cap. As a result, a depositor can call `depositETH()` with an arbitrarily large `msg.value` as long as the current total is at or below the limit, and the check will pass.

The public view helper `getAssetCurrentLimit()` (lines 402–409) correctly computes the remaining room as `depositLimit - totalAssetDeposits`, but that value is never enforced for ETH in the actual deposit path.

### Impact Explanation
Any unprivileged user can deposit ETH in excess of the admin-configured `depositLimitByAsset` cap. The cap is a protocol safety invariant — it bounds how much ETH the protocol takes on at any given time (e.g., to stay within EigenLayer strategy capacity or validator limits). Bypassing it causes more rsETH to be minted than the limit allows and may push downstream operations (EigenLayer deposits, validator staking) into states they were not designed to handle, potentially causing those operations to revert or the protocol to become temporarily unable to process further deposits or withdrawals.

**Impact level: Medium — Temporary freezing of funds / contract fails to deliver promised returns.**

### Likelihood Explanation
The entry path is the public, permissionless `depositETH()` function. No special role, front-running, or external dependency is required. Any depositor who observes that `totalAssetDeposits` is close to (but not yet over) the limit can send a single transaction with `msg.value` large enough to exceed it. Likelihood is **High**.

### Recommendation
Add `amount` to the ETH branch so it matches the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 1000 ether`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` returns `999 ether`.
3. User calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 ether > 1000 ether` → `false` → deposit proceeds.
5. Total ETH in protocol is now `1499 ether`, 49.9% above the configured cap, with `rsETH` minted for the full `500 ether`. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L399-409)
```text
    /// @notice gets the current limit of asset deposit
    /// @param asset Asset address
    /// @return currentLimit Current limit of asset deposit
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
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
