### Title
Incorrect ETH Deposit Limit Check Allows Bypassing the Deposit Cap - (File: contracts/LRTDepositPool.sol)

### Summary
In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`, the ETH branch omits the incoming deposit `amount` from the limit comparison, while the ERC20 branch correctly includes it. This means any depositor can push the ETH total above the configured deposit cap.

### Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` has two branches:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← missing + amount
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For ETH the function returns `true` (i.e., "limit exceeded, reject") only when the **current** total already exceeds the cap — it never accounts for the new `amount` being deposited. For every ERC20 asset the check is `totalAssetDeposits + amount > limit`, which is correct.

The analog to the external report is exact: a guard condition whose comparison is wrong causes a path that should be rejected (deposit over the cap) to always proceed.

### Impact Explanation
Any unprivileged caller can invoke `depositETH` when `totalAssetDeposits ≤ depositLimit` but `totalAssetDeposits + msg.value > depositLimit`. The deposit succeeds, minting rsETH, and the ETH total silently exceeds the configured cap. The deposit limit — a risk-management control set by the admin — is never enforced for ETH. The contract fails to deliver its promised deposit-cap guarantee.

### Likelihood Explanation
The condition is triggered every time a depositor sends enough ETH to cross the cap boundary. No special permissions are required; `depositETH` is open to any caller. As the pool approaches its ETH limit this becomes increasingly likely with every large deposit.

### Recommendation
Add `amount` to the ETH branch, matching the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100 ether`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` returns `99 ether`.
3. Attacker calls `depositETH{value: 50 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 50 ether)` evaluates `99 ether > 100 ether` → `false` → limit not exceeded.
5. Deposit proceeds; total ETH in protocol becomes `149 ether`, 49 ETH above the configured cap.
6. For comparison, an ERC20 deposit of the same size would evaluate `99 ether + 50 ether > 100 ether` → `true` → correctly reverted with `MaximumDepositLimitReached`. [1](#0-0) [2](#0-1) [3](#0-2)

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
