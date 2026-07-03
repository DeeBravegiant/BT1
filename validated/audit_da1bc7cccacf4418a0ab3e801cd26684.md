### Title
ETH Deposit Limit Not Enforced on Incoming Amount — (`File: contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies two different limit-check formulas depending on asset type. For ERC20 tokens the incoming `amount` is included in the comparison; for native ETH it is omitted entirely. Any unprivileged depositor can push total ETH deposits above the admin-configured cap.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` is the sole gate that enforces the per-asset deposit cap before rsETH is minted:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount included
}
``` [1](#0-0) 

For ETH the check is `totalAssetDeposits > limit` (strict greater-than, no `amount`). For ERC20 it is `totalAssetDeposits + amount > limit`. Consequently:

- When `totalAssetDeposits == limit` the ETH branch returns `false` (not exceeded) and the deposit proceeds.
- After the deposit `totalAssetDeposits` becomes `limit + msg.value`, breaching the cap.
- For ERC20 the same scenario returns `true` and correctly reverts.

The check is invoked unconditionally inside `_beforeDeposit`, which is called by both `depositETH` and `depositAsset`: [2](#0-1) 

`depositETH` is the public entry point reachable by any user: [3](#0-2) 

---

### Impact Explanation

The deposit limit is a protocol safety cap. Bypassing it allows more rsETH to be minted than the admin intended. Because rsETH price is recalculated from actual TVL on every `updateRSETHPrice` call, no immediate insolvency results; however the protocol silently violates its own invariant. This maps to **Low — contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

Any ETH depositor can trigger this condition the moment `getTotalAssetDeposits(ETH_TOKEN)` equals the configured limit. No special privilege, timing, or front-running is required. The condition is reachable in normal operation as the cap fills up.

---

### Recommendation

Include the incoming `amount` in the ETH branch, matching the ERC20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

---

### Proof of Concept

1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Protocol accumulates exactly `1000 ether` in total ETH deposits.
3. Alice calls `depositETH{value: 100 ether}(...)`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 100 ether)` evaluates `1000 ether > 1000 ether` → `false` → no revert.
5. `_mintRsETH` mints rsETH for Alice; total ETH deposits become `1100 ether`, 10 % above the configured cap.
6. The same call with any ERC20 at the same fill level would have reverted with `MaximumDepositLimitReached`. [1](#0-0) [4](#0-3)

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
