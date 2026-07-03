### Title
Missing Zero-Value Check in `updateAssetDepositLimit` Allows Manager to Freeze All Deposits for a Supported Asset - (File: contracts/LRTConfig.sol)

### Summary
`LRTConfig.updateAssetDepositLimit` lacks the zero-value guard that `_addNewSupportedAsset` enforces. A manager can accidentally set `depositLimitByAsset[asset]` to `0`, which causes `_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool` to always return `true`, permanently blocking all new deposits for that asset until corrected.

### Finding Description
When a new asset is first registered, `_addNewSupportedAsset` enforces the invariant that the deposit limit must be non-zero:

```solidity
// contracts/LRTConfig.sol
function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
    UtilLib.checkNonZeroAddress(asset);
    if (depositLimit == 0) {
        revert InvalidDepositLimit();   // ← guard present
    }
    ...
    depositLimitByAsset[asset] = depositLimit;
}
```

However, the update path has no equivalent guard:

```solidity
// contracts/LRTConfig.sol
function updateAssetDepositLimit(
    address asset,
    uint256 depositLimit          // ← no zero-check
)
    external
    onlyRole(LRTConstants.MANAGER)
    onlySupportedAsset(asset)
{
    depositLimitByAsset[asset] = depositLimit;   // can be set to 0
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
```

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` then evaluates:

```solidity
// contracts/LRTDepositPool.sol
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

With `depositLimitByAsset[asset] == 0`, the right-hand side is `0`, so the expression is `true` for any non-zero `amount`, causing `_beforeDeposit` to revert with `MaximumDepositLimitReached` for every caller.

### Impact Explanation
Any user calling `depositAsset` or `depositETH` (via `LRTDepositPool`) for the affected asset will have their transaction reverted. Funds already deposited are unaffected, but no new deposits can enter the protocol for that asset until the manager corrects the value. This constitutes a **temporary freezing of funds** (Medium severity per the allowed impact scope).

### Likelihood Explanation
The `MANAGER` role is a trusted operational key used for routine parameter updates. The absence of a guard makes a zero-value typo or scripting error a realistic human-error scenario, directly analogous to the external report's stated concern: *"to avoid human error that would result in breaking core system invariants."* The invariant `depositLimit > 0` is explicitly enforced at creation time but silently dropped at update time.

### Recommendation
Mirror the guard from `_addNewSupportedAsset` in `updateAssetDepositLimit`:

```solidity
function updateAssetDepositLimit(address asset, uint256 depositLimit)
    external
    onlyRole(LRTConstants.MANAGER)
    onlySupportedAsset(asset)
{
    if (depositLimit == 0) revert InvalidDepositLimit();   // add this
    depositLimitByAsset[asset] = depositLimit;
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
```

### Proof of Concept
1. Manager calls `LRTConfig.updateAssetDepositLimit(stETH, 0)` — no revert, state is written.
2. Any user calls `LRTDepositPool.depositAsset(stETH, 1 ether, 0, "")`.
3. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(stETH, 1 ether)`.
4. `totalAssetDeposits + 1 ether > 0` → `true` → reverts with `MaximumDepositLimitReached`.
5. All stETH deposits are frozen until the manager issues a corrective call. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTConfig.sol (L106-118)
```text
    function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
        UtilLib.checkNonZeroAddress(asset);
        if (depositLimit == 0) {
            revert InvalidDepositLimit();
        }
        if (isSupportedAsset[asset]) {
            revert AssetAlreadySupported();
        }
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
        depositLimitByAsset[asset] = depositLimit;
        emit AddedNewSupportedAsset(asset, depositLimit);
    }
```

**File:** contracts/LRTConfig.sol (L123-133)
```text
    function updateAssetDepositLimit(
        address asset,
        uint256 depositLimit
    )
        external
        onlyRole(LRTConstants.MANAGER)
        onlySupportedAsset(asset)
    {
        depositLimitByAsset[asset] = depositLimit;
        emit AssetDepositLimitUpdate(asset, depositLimit);
    }
```

**File:** contracts/LRTDepositPool.sol (L657-663)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
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
