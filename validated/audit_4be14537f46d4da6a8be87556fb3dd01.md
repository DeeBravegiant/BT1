### Title
ETH Deposit Limit Check Omits Incoming `msg.value`, Allowing Cap to Be Exceeded - (File: contracts/LRTDepositPool.sol)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check for ETH: it tests only whether the *current* total already exceeds the limit, without adding the incoming deposit amount. Any depositor can push the ETH TVL past the configured cap.

### Finding Description
`depositETH` calls `_beforeDeposit`, which calls `_checkIfDepositAmountExceedesCurrentLimit`. For ERC-20 assets the check correctly includes the incoming amount:

```solidity
// contracts/LRTDepositPool.sol:681
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

But for ETH the `amount` parameter is silently dropped:

```solidity
// contracts/LRTDepositPool.sol:679
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
```

The function signature receives `amount` for ETH too (it is `msg.value` passed from `_beforeDeposit`), but the comparison ignores it. As a result, when `totalAssetDeposits == depositLimit` the check returns `false` (not exceeded), the deposit proceeds, and after the call `totalAssetDeposits = depositLimit + msg.value`.

This is the direct analog of the reported pattern: a cap-enforcement path that calls an internal helper which omits the cap check for a specific code branch, allowing the cap to be silently bypassed.

### Impact Explanation
The ETH deposit cap is a protocol-level risk-management invariant. Any depositor can violate it whenever the running ETH total is at or near the limit, minting rsETH beyond the intended ceiling. This constitutes a failure to deliver the promised protocol constraint without direct loss of value.

**Impact: Low** — contract fails to deliver promised returns (deposit cap invariant), but no direct fund loss.

### Likelihood Explanation
The condition is trivially reachable: any unprivileged depositor calling `depositETH` when `totalAssetDeposits >= depositLimit` triggers the bypass. No special role, timing, or front-running is required.

**Likelihood: High**

### Recommendation
Include the incoming ETH amount in the cap check, consistent with the ERC-20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Protocol accumulates `totalAssetDeposits(ETH) = 1000 ether` (exactly at cap).
3. Attacker calls `depositETH{value: 100 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 100 ether)` evaluates `1000 ether > 1000 ether` → `false` → no revert.
5. `_mintRsETH` mints rsETH for 100 ETH; `totalAssetDeposits(ETH)` is now 1100 ether — 10 % above the configured cap. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** contracts/LRTDepositPool.sol (L648-663)
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
