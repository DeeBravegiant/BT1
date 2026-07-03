### Title
ETH Deposit Limit Check Omits `msg.value`, Allowing Unlimited ETH Deposits Beyond the Configured Cap - (File: `contracts/LRTDepositPool.sol`)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric limit check: for ERC-20 LST assets it correctly tests `totalAssetDeposits + amount > depositLimit`, but for ETH it only tests `totalAssetDeposits > depositLimit`, omitting the incoming `msg.value`. Any depositor can therefore bypass the ETH deposit cap entirely.

### Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` in `contracts/LRTDepositPool.sol` (lines 676–682) contains a branch for ETH that does not add the incoming deposit amount to the current total before comparing against the configured limit:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount not included
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for LST
}
``` [1](#0-0) 

For ETH the function returns `true` (i.e., "limit exceeded") only when the *pre-deposit* total already exceeds the limit. As long as the current total is at or below the limit, the check passes regardless of how large `msg.value` is. The correct check should be `totalAssetDeposits + amount > depositLimit`, mirroring the LST branch.

This check is the sole guard inside `_beforeDeposit`:

```solidity
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [2](#0-1) 

which is called by the public `depositETH` entry point:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    ...
}
``` [3](#0-2) 

### Impact Explanation
The ETH deposit limit (`depositLimitByAsset[ETH_TOKEN]`) is a risk-management control that caps total ETH exposure in the protocol. Because the incoming `msg.value` is never added to `totalAssetDeposits` before the comparison, a single depositor can push the protocol's ETH holdings arbitrarily far above the configured cap in one transaction. The limit mechanism fails to deliver its promised protection for ETH, while working correctly for all LST assets.

**Impact: Low** — Contract fails to deliver promised returns (the deposit cap is non-functional for ETH); no direct fund loss or freeze occurs.

### Likelihood Explanation
The entry path is fully permissionless (`depositETH` is callable by any address). No special role, front-running, or external dependency is required. Any depositor who observes that `totalAssetDeposits` is near but below the limit can immediately exploit this in a single transaction.

**Likelihood: High** — Trivially reachable by any unprivileged depositor.

### Recommendation
Add `amount` to `totalAssetDeposits` in the ETH branch, matching the LST branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Alice calls `depositETH` with `msg.value = 999 ether`. `totalAssetDeposits` becomes 999 ETH. Check: `999 > 1000` → false → deposit accepted. ✓
3. Bob calls `depositETH` with `msg.value = 5000 ether`. `totalAssetDeposits` is still 999 ETH at check time. Check: `999 > 1000` → false → deposit accepted. ✓
4. Protocol now holds 5999 ETH, nearly 6× the intended cap, with no revert.

For LST assets the same scenario would correctly revert at step 3 because `999 + 5000 > 1000` evaluates to `true`.

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
