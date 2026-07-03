### Title
`depositLimitByAsset` can be set below `totalAssetDeposits`, permanently blocking new deposits - (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.updateAssetDepositLimit` sets a new per-asset deposit cap without verifying that the current total deposits for that asset are still within the new cap. If the new limit is set below the already-accumulated deposits, every subsequent deposit call in `LRTDepositPool` reverts, freezing new inflows for that asset until the limit is manually raised.

---

### Finding Description

`updateAssetDepositLimit` in `LRTConfig.sol` unconditionally overwrites `depositLimitByAsset[asset]` with the caller-supplied value:

```solidity
function updateAssetDepositLimit(
    address asset,
    uint256 depositLimit
)
    external
    onlyRole(LRTConstants.MANAGER)
    onlySupportedAsset(asset)
{
    depositLimitByAsset[asset] = depositLimit;          // no check vs. current deposits
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
``` [1](#0-0) 

Every deposit path in `LRTDepositPool` (`depositETH`, `depositAsset`) calls `_beforeDeposit`, which calls `_checkIfDepositAmountExceedesCurrentLimit`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [2](#0-1) 

Once `depositLimitByAsset[asset] < totalAssetDeposits`:

- **ETH path**: `totalAssetDeposits > depositLimitByAsset[asset]` → `true` → every ETH deposit reverts with `MaximumDepositLimitReached`.
- **ERC-20 path**: `totalAssetDeposits + amount > depositLimitByAsset[asset]` → `true` for any `amount > 0` → every LST deposit reverts. [3](#0-2) 

The broken invariant (`depositLimitByAsset[asset] >= totalAssetDeposits`) persists until the MANAGER issues a corrective call to raise the limit again.

---

### Impact Explanation

All new deposits for the affected asset are blocked for as long as the limit remains below the current total deposits. Existing depositor funds are not lost, but the protocol fails to accept new inflows — a **temporary freeze of funds** (new deposits) for any asset whose limit is misconfigured. Impact: **Medium**.

---

### Likelihood Explanation

The MANAGER role is a privileged but operationally active role that adjusts deposit limits as part of normal protocol management (e.g., reducing a cap during a risk event). A manager reducing the cap without first querying `getTotalAssetDeposits` — a separate call to a separate contract — can trivially produce this state. No malicious intent is required; a routine misconfiguration suffices. Likelihood: **Low**.

---

### Recommendation

Add a guard in `updateAssetDepositLimit` that fetches the current total deposits and rejects any new limit that would fall below them:

```solidity
function updateAssetDepositLimit(
    address asset,
    uint256 depositLimit
)
    external
    onlyRole(LRTConstants.MANAGER)
    onlySupportedAsset(asset)
{
    address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);
    uint256 currentDeposits = ILRTDepositPool(depositPool).getTotalAssetDeposits(asset);
    require(depositLimit >= currentDeposits, "LRTConfig: limit below current deposits");

    depositLimitByAsset[asset] = depositLimit;
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
```

This mirrors the pattern already used in `removeSupportedAsset`, which checks `getTotalAssetDeposits` before allowing the removal. [4](#0-3) 

---

### Proof of Concept

1. Suppose `stETH` has accumulated `totalAssetDeposits = 90,000 ether` and the current limit is `100,000 ether`.
2. MANAGER calls `updateAssetDepositLimit(stETH, 80_000 ether)` intending to tighten the cap.
3. `depositLimitByAsset[stETH]` is now `80,000 ether` — below the existing `90,000 ether` in the protocol.
4. Any user calling `depositAsset(stETH, amount, ...)`:
   - `_checkIfDepositAmountExceedesCurrentLimit` evaluates `90,000 + amount > 80,000` → `true`.
   - `_beforeDeposit` reverts with `MaximumDepositLimitReached`.
5. All stETH deposits are frozen until the MANAGER issues a corrective `updateAssetDepositLimit` call with a value ≥ `90,000 ether`.

### Citations

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
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

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
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
