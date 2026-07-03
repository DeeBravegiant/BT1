### Title
ETH Deposit Limit Bypass Due to Missing `amount` Operand in Bounds Check - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric bounds check: for ERC-20 assets it correctly tests `totalAssetDeposits + amount > depositLimit`, but for ETH it omits `amount` and only tests `totalAssetDeposits > depositLimit`. Any depositor can therefore push the protocol's ETH TVL above the configured deposit cap in a single call to `depositETH`.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` is the sole guard that enforces the per-asset deposit cap before rsETH is minted:

```solidity
// contracts/LRTDepositPool.sol  L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
``` [1](#0-0) 

The ETH branch evaluates whether the **current** total already exceeds the limit, not whether the **post-deposit** total would exceed it. The correct expression, matching the ERC-20 branch, is `totalAssetDeposits + amount > depositLimit`.

The function is called unconditionally from `_beforeDeposit`, which is invoked by every `depositETH` call:

```solidity
// contracts/LRTDepositPool.sol  L661-L663
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [2](#0-1) 

---

### Impact Explanation

**Classification**: Share/asset mis-accounting — deposit-limit safety cap bypass.

Suppose `depositLimit = 1 000 ETH` and `totalAssetDeposits = 999 ETH`. The check `999 > 1 000` is `false`, so a depositor can send `500 ETH` in a single transaction. After the call `totalAssetDeposits = 1 499 ETH`, 49.9 % above the cap, and the protocol mints rsETH for the full 500 ETH. The overshoot is bounded only by the depositor's wallet balance, not by the configured limit.

Consequences:
- rsETH is minted beyond the intended safety ceiling, diluting the backing ratio.
- The protocol's risk exposure to EigenLayer strategies exceeds the operator-configured maximum.
- `getAssetCurrentLimit` will return `0` after the overshoot, but the damage (excess minting) is already done.

Impact maps to **Low — contract fails to deliver promised returns** (the deposit cap is a promised protocol constraint), with potential escalation to **Medium** if the excess ETH cannot be deployed into EigenLayer strategies and becomes stranded.

---

### Likelihood Explanation

The entry path is the public, permissionless `depositETH` function. No role, whitelist, or special condition is required. Any depositor who monitors on-chain state and sees `totalAssetDeposits` approaching `depositLimit` can exploit this in a single transaction. Likelihood is **High**.

---

### Recommendation

Add `amount` to the ETH branch, mirroring the ERC-20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [3](#0-2) 

---

### Proof of Concept

1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 1 000 ether`.
2. Protocol accumulates `totalAssetDeposits(ETH) = 999 ether` through normal usage.
3. Attacker calls `depositETH{value: 500 ether}(minRSETH, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 > 1 000` → `false` → no revert.
5. `_mintRsETH` mints rsETH for the full 500 ETH.
6. `getTotalAssetDeposits(ETH)` now returns `1 499 ether`, 49.9 % above the cap.
7. The ERC-20 path for the same scenario would have evaluated `999 + 500 > 1 000` → `true` → `MaximumDepositLimitReached` revert, correctly blocking the deposit. [4](#0-3) [5](#0-4)

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
