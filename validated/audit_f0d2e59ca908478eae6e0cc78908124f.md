### Title
ETH Deposit Limit Bypass Due to Missing Amount in Limit Check — (`File: contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies two different comparison expressions depending on whether the deposited asset is ETH or an ERC-20 LST. The ETH branch omits the incoming deposit `amount` from the check, so the deposit limit is never enforced against the size of the incoming ETH deposit — only against the pre-existing total. Any depositor can push ETH holdings arbitrarily beyond the configured cap in a single transaction.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` contains an asset-type branch:

```solidity
// contracts/LRTDepositPool.sol  L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount absent
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount present
}
```

For every ERC-20 LST the guard correctly evaluates `totalAssetDeposits + amount > limit`, blocking any deposit that would push the running total past the cap. For ETH the guard evaluates only `totalAssetDeposits > limit`, which is already true only if the cap was breached by a prior deposit. The size of the current deposit is never considered.

Consequently, when `totalAssetDeposits ≤ limit`, the ETH branch always returns `false` regardless of `amount`, and `_beforeDeposit` proceeds to mint rsETH for the full deposit. [1](#0-0) 

The function is called unconditionally from `_beforeDeposit`, which is the sole pre-flight check for both `depositETH` and `depositAsset`: [2](#0-1) 

`depositETH` is a public, permissionless entry point: [3](#0-2) 

The view helper `getAssetCurrentLimit` uses `>` (strictly greater than) for both asset types, so it correctly reports `0` remaining capacity when `totalAssetDeposits == limit`. This creates an additional inconsistency: the public view says "no capacity left" while the enforcement gate still allows the deposit. [4](#0-3) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns.**

The deposit limit is a risk-management parameter set by the admin to cap protocol exposure to any single asset. For ETH, this cap is unenforceable: a single depositor can send an amount equal to the entire remaining limit plus any additional ETH in one call, minting a proportional amount of rsETH. The protocol then holds more ETH than it was designed to manage, potentially exceeding the capacity of the NodeDelegator / EigenLayer strategy pipeline. Excess ETH sits idle in the deposit pool, diluting the effective yield backing rsETH and violating the protocol's stated deposit constraints without any loss of principal.

---

### Likelihood Explanation

**High.** The vulnerable path is the primary, permissionless ETH deposit function. No special role, timing, or market condition is required. Any depositor who observes that `totalAssetDeposits ≤ limit` can exploit this in a single transaction. The discrepancy is structural and present in every deployment.

---

### Recommendation

Apply the same expression used for ERC-20 assets to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
-   if (asset == LRTConstants.ETH_TOKEN) {
-       return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
-   }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This makes the ETH and LST paths consistent and ensures that no single deposit — regardless of asset type — can push the running total past the configured cap.

---

### Proof of Concept

Assume `depositLimitByAsset(ETH_TOKEN) = 1000 ETH` and `getTotalAssetDeposits(ETH_TOKEN) = 999 ETH`.

1. Alice calls `depositETH{value: 500 ETH}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500 ETH)`.
3. ETH branch evaluates `999 ETH > 1000 ETH` → `false` → limit not exceeded.
4. `_mintRsETH` mints rsETH for 500 ETH.
5. `getTotalAssetDeposits(ETH_TOKEN)` is now `1499 ETH` — 49.9 % above the configured cap.

The same call with any ERC-20 LST at `totalDeposits = 999` and `amount = 500` would evaluate `999 + 500 > 1000` → `true` → `MaximumDepositLimitReached` revert, demonstrating the inconsistency. [1](#0-0)

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
