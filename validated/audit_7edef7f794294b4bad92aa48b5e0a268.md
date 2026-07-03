### Title
ETH Deposit Limit Not Enforced — Deposit Amount Excluded from Cap Check in `_checkIfDepositAmountExceedesCurrentLimit()` - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit()` applies an asymmetric limit check: for ERC-20 assets it correctly adds the incoming `amount` to `totalAssetDeposits` before comparing against the configured cap, but for ETH it omits `amount` entirely. As a result, the ETH deposit limit is never enforced on the size of an individual deposit — any depositor can push total ETH holdings arbitrarily above the configured cap in a single transaction.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` contains a branch for ETH that reads:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
``` [1](#0-0) 

The ETH branch checks only whether the *current* total already exceeds the limit — it never adds the incoming `amount`. The ERC-20 branch correctly checks `totalAssetDeposits + amount > limit`.

`_beforeDeposit` calls this function and reverts on `true`:

```solidity
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [2](#0-1) 

Because the ETH branch never includes `depositAmount`, the revert is only triggered when `totalAssetDeposits` already exceeds the limit from a prior deposit. A depositor whose transaction is the one that crosses the limit will never be rejected — the check passes, the deposit is accepted, and `totalAssetDeposits` ends up above the cap.

The public entry point is `depositETH()`, callable by any user:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
``` [3](#0-2) 

---

### Impact Explanation

The ETH deposit limit (`depositLimitByAsset[ETH_TOKEN]`) is a protocol-level risk cap set by the manager via `LRTConfig.updateAssetDepositLimit`. Its purpose is to bound the total ETH exposure the protocol accepts. Because the cap is never enforced on the deposit amount itself, a single depositor can exceed it by an arbitrary margin in one transaction, minting a proportionally large rsETH position. This breaks the protocol's stated risk controls and constitutes a failure to deliver the promised deposit-cap guarantee.

**Impact: Low** — the contract fails to deliver its promised deposit-limit invariant. No direct theft or permanent freeze occurs, but the protocol's risk management is silently bypassed.

---

### Likelihood Explanation

Any unprivileged user calling `depositETH()` with `msg.value` large enough to push total ETH deposits past the limit will trigger this path. No special role, front-running, or oracle manipulation is required. The condition is reachable whenever the ETH deposit limit has not already been exceeded.

---

### Recommendation

Include `amount` in the ETH branch, mirroring the ERC-20 branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This makes the check uniform across all asset types and correctly rejects any deposit that would push total holdings above the configured cap.

---

### Proof of Concept

1. Admin sets ETH deposit limit to 1 000 ETH via `LRTConfig.updateAssetDepositLimit`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` = 999 ETH.
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. Inside `_checkIfDepositAmountExceedesCurrentLimit`:
   - `totalAssetDeposits = 999e18`
   - ETH branch: `999e18 > 1000e18` → `false` → no revert.
5. Deposit succeeds; total ETH in protocol becomes 1 499 ETH — 499 ETH above the configured cap.
6. For ERC-20 assets the same 500-unit deposit at 999 units total would evaluate `999 + 500 > 1000` → `true` → revert. The asymmetry is the root cause. [4](#0-3)

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
