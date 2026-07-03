### Title
ETH Deposit Limit Check Uses Wrong Variable, Omitting `amount` — (`File: contracts/LRTDepositPool.sol`)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check: for ERC-20 assets it correctly tests `totalAssetDeposits + amount > limit`, but for ETH it tests only `totalAssetDeposits > limit`, omitting the incoming deposit amount. Any depositor can therefore push ETH holdings above the configured cap.

### Finding Description
In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`, the ETH branch uses the wrong expression:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← `amount` is absent
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for ERC-20
}
``` [1](#0-0) 

The ETH branch returns `true` (i.e., "limit exceeded") only when the limit has **already** been surpassed before the current call. It never accounts for the new `amount` being deposited. The ERC-20 branch correctly adds `amount` to the running total before comparing.

This is called from `_beforeDeposit`, which is the sole guard invoked by the public `depositETH` entry point:

```solidity
// contracts/LRTDepositPool.sol  lines 648-670
function _beforeDeposit(...) private view returns (uint256 rsethAmountToMint) {
    ...
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
        revert MaximumDepositLimitReached();
    }
    ...
}
``` [2](#0-1) 

### Impact Explanation
Any unprivileged depositor can call `depositETH` and push the protocol's ETH holdings arbitrarily above the configured `depositLimitByAsset` cap. The cap is the protocol's primary risk-management control for ETH exposure (e.g., EigenLayer strategy capacity, slashing risk). Bypassing it means the protocol silently accepts more ETH than it was designed to handle, violating the invariant that `getTotalAssetDeposits(ETH) ≤ depositLimit`. This constitutes **contract fails to deliver promised returns** (Low).

### Likelihood Explanation
The entry point `depositETH` is public, requires no special role, and is callable by any user. The condition is triggered whenever `totalAssetDeposits == depositLimit` (the limit is exactly met), which is a normal operational state. Likelihood is **high** once the ETH cap is reached.

### Recommendation
Apply the same `+ amount` pattern to the ETH branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 1000 ether`.
2. Legitimate deposits bring `getTotalAssetDeposits(ETH_TOKEN)` to exactly `1000 ether`.
3. At this point `totalAssetDeposits > limit` is `false`, so `_checkIfDepositAmountExceedesCurrentLimit` returns `false`.
4. An unprivileged depositor calls `depositETH{value: 500 ether}(...)`.
5. `_beforeDeposit` does not revert; the deposit succeeds and `getTotalAssetDeposits(ETH_TOKEN)` becomes `1500 ether` — 50 % above the configured cap.
6. The depositor receives rsETH minted at the current exchange rate; the protocol now holds more ETH than its risk parameters allow. [3](#0-2) [4](#0-3) [1](#0-0)

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
