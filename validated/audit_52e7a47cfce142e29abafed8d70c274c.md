### Title
ETH Deposit Limit Check Omits Incoming Deposit Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

---

### Summary

`_checkIfDepositAmountExceedesCurrentLimit()` in `LRTDepositPool.sol` applies divergent logic for ETH versus ERC20 assets. For ERC20 it correctly tests `totalAssetDeposits + amount > limit`, but for ETH it tests only `totalAssetDeposits > limit`, omitting the incoming deposit amount. Any unprivileged depositor can therefore push the ETH TVL above the configured cap in a single call.

---

### Finding Description

The function at lines 676–682 of `LRTDepositPool.sol` branches on asset type:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount not included
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For ETH the guard fires only when the protocol is **already** over the limit. The new `msg.value` is never added to `totalAssetDeposits` before the comparison, so a deposit that would push the total from just-below-limit to far-above-limit passes the check and proceeds.

This is called unconditionally from `_beforeDeposit()`, which is the sole pre-flight check inside the public `depositETH()` entry point:

```solidity
// LRTDepositPool.sol:648-670
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected)
    private view returns (uint256 rsethAmountToMint)
{
    ...
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
        revert MaximumDepositLimitReached();
    }
    ...
}
```

The ERC20 path (`depositAsset`) passes the same `amount` argument and is checked correctly. The divergence is exclusive to the ETH path.

---

### Impact Explanation

The deposit limit (`depositLimitByAsset`) is the protocol's primary risk-management cap on how much ETH can enter the system. Bypassing it means the protocol silently accepts more ETH than governance has approved, inflating TVL beyond the intended ceiling. No funds are directly stolen, but the protocol fails to deliver its promised risk boundary.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The entry path is fully unprivileged. Any user who calls `depositETH()` when `totalAssetDeposits(ETH) ≤ limit` can exceed the limit in a single transaction. No special role, front-running, or external dependency is required. The condition is routinely met during normal protocol operation.

**Likelihood: High** — the divergence is always present and the triggering condition (deposits near the cap) is a normal operational state.

---

### Recommendation

Add `+ amount` to the ETH branch so both paths are consistent:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    // unified check: include the incoming amount for both ETH and ERC20
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The `asset == LRTConstants.ETH_TOKEN` branch can be removed entirely because the arithmetic is identical for both cases.

---

### Proof of Concept

1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 1 000 ETH`.
2. Protocol accumulates `totalAssetDeposits(ETH) = 999.9 ETH` through normal deposits.
3. Attacker calls `depositETH{value: 500 ETH}(0, "")`.
4. Inside `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500 ETH)`:
   - `totalAssetDeposits = 999.9 ETH`
   - ETH branch: `999.9 > 1 000` → **false** → limit not exceeded
   - Deposit proceeds; total ETH in protocol becomes **1 499.9 ETH**, 50 % above the cap.
5. For comparison, if the same user tried `depositAsset(stETH, 500 ether, ...)`:
   - ERC20 branch: `999.9 + 500 > 1 000` → **true** → `MaximumDepositLimitReached` reverts.

The divergence is structurally identical to the external report's pattern: one code path checks a condition completely (`totalAssetDeposits + amount`) while the analogous ETH path omits a critical term (`amount`), producing inconsistent enforcement of the same invariant. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/LRTConfig.sol (L23-23)
```text
    mapping(address token => uint256 amount) public depositLimitByAsset;
```
