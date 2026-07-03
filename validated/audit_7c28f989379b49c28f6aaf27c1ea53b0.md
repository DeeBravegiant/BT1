### Title
ETH Deposit Limit Bypass Due to Missing `amount` in Bounds Check — (`File: contracts/LRTDepositPool.sol`)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric bounds check: for LST assets it correctly tests `totalAssetDeposits + amount > limit`, but for ETH it omits the incoming deposit amount entirely, testing only `totalAssetDeposits > limit`. Any depositor can therefore send an arbitrarily large ETH deposit and bypass the configured cap, causing the protocol to accept and restake more ETH than governance intended.

### Finding Description
In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` (lines 676–682), the ETH branch reads:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

The parameter `amount` — which carries `msg.value` from `depositETH` — is never added to `totalAssetDeposits` before the comparison. The check therefore only asks "has the limit already been exceeded before this deposit?" rather than "will this deposit push the total over the limit?". As long as `totalAssetDeposits ≤ limit`, the function returns `false` (not exceeded) regardless of how large `amount` is, and `_beforeDeposit` proceeds to mint rsETH.

The LST path is correct: `totalAssetDeposits + amount > limit` properly accounts for the incoming deposit. The ETH path is structurally identical to the external report's `txCalldataLen < offset` bug — the boundary value (`amount` / the new deposit) is excluded from the comparison, so the guard passes when it should reject.

### Impact Explanation
The ETH deposit cap (`depositLimitByAsset[ETH_TOKEN]`) is the protocol's primary risk-management control over EigenLayer exposure. With the check broken:

- A single depositor can send any ETH amount (e.g., 10× the configured limit) in one transaction while `totalAssetDeposits ≤ limit`.
- The protocol mints rsETH proportional to the full deposit, permanently increasing EigenLayer exposure beyond the intended ceiling.
- If EigenLayer strategies are slashed, the excess exposure translates directly to rsETH holder losses and potential protocol insolvency.

**Impact: Medium — deposit limit bypass enabling unbounded ETH intake and EigenLayer over-exposure.**

### Likelihood Explanation
The entry point is the public, permissionless `depositETH()` function. No role, key, or special condition is required. Any ETH holder can trigger the bypass in a single transaction at any time the protocol is unpaused and `totalAssetDeposits ≤ limit` (the normal operating state). Likelihood is **High**.

### Recommendation
Include `amount` in the ETH branch, mirroring the LST branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The ETH-specific branch is no longer needed once the check is unified.

### Proof of Concept

Assume `depositLimitByAsset[ETH_TOKEN] = 100 ether` and `getTotalAssetDeposits(ETH) = 50 ether`.

1. Attacker calls `depositETH{value: 10_000 ether}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 10_000 ether)`.
3. Inside the function: `totalAssetDeposits = 50 ether`; ETH branch executes `return (50 ether > 100 ether)` → `false`.
4. The `amount` parameter `10_000 ether` is never evaluated.
5. `_checkIfDepositAmountExceedesCurrentLimit` returns `false` → no revert.
6. `getRsETHAmountToMint` is called for `10_000 ether`; rsETH is minted; ETH is held in the pool and later forwarded to NodeDelegators and EigenLayer — 100× the intended cap. [1](#0-0) [2](#0-1) [3](#0-2)

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
