### Title
ETH Deposit Limit Not Enforced Due to Missing `amount` in Cumulative Check - (`contracts/LRTDepositPool.sol`)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies the deposit limit check inconsistently: for LST assets it correctly includes the incoming `amount` in the comparison, but for ETH it omits `amount` entirely. This mirrors the external report's root cause — a validation check that ignores an already-relevant quantity — and allows the ETH deposit cap to be exceeded by an arbitrary amount in a single transaction.

### Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` is called from `_beforeDeposit`, which gates both `depositETH` and `depositAsset`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for LSTs
}
```

For ETH the function returns `true` (limit exceeded) only when `totalAssetDeposits` **already** exceeds the cap, completely ignoring the `amount` being deposited right now. For every LST the check is `totalAssetDeposits + amount > limit`, which is the correct cumulative form.

The external report's bug is structurally identical: a validation check that should evaluate `existing + new` instead evaluates only one of the two quantities, producing wrong enforcement.

### Impact Explanation
Any user can call `depositETH` with an arbitrarily large `msg.value` the moment `totalAssetDeposits` is anywhere below the configured cap. A single transaction can push the total ETH deposited into EigenLayer far above the intended limit. Because the deposit limit exists to bound protocol exposure to EigenLayer slashing risk, breaching it can cause protocol insolvency (Critical) or at minimum violates the intended risk parameters and could temporarily freeze user funds if the protocol is forced to unwind excess positions.

### Likelihood Explanation
The entry path is fully permissionless — `depositETH` has no role restriction, only `whenNotPaused` and `onlySupportedAsset`. Any depositor can trigger this in a single call whenever `totalAssetDeposits ≤ depositLimit`. No special conditions or timing are required.

### Recommendation
Add `amount` to the ETH branch of the check, matching the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 1000 ether`.
2. Current `totalAssetDeposits(ETH_TOKEN) = 999 ether` (one wei below the cap).
3. Alice calls `depositETH{value: 500 ether}(...)`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 ether > 1000 ether` → `false` → limit not exceeded.
5. 500 ETH is accepted; `totalAssetDeposits` becomes 1499 ETH — 49.9 % above the intended cap.
6. The same call with an LST would have evaluated `999 + 500 > 1000` → `true` → correctly reverted. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** contracts/LRTDepositPool.sol (L648-669)
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
