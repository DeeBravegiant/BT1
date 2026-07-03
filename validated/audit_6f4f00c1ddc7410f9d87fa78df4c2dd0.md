### Title
ETH Deposit Limit Check Omits New Deposit Amount, Allowing Limit Bypass - (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an incorrect validation for ETH deposits. The ERC20 branch correctly checks `totalAssetDeposits + amount > depositLimit`, but the ETH branch only checks `totalAssetDeposits > depositLimit`, omitting the incoming deposit amount. This is the direct analog of the TraitForge bug: a guard is applied to the wrong level of the value (the pre-deposit aggregate instead of the post-deposit aggregate), allowing the deposit limit to be silently exceeded.

---

### Finding Description

In `_checkIfDepositAmountExceedesCurrentLimit`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← missing `+ amount`
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
``` [1](#0-0) 

The ETH branch returns `true` (i.e., "limit exceeded, reject") only when the **current** total already exceeds the limit. It never considers the incoming `amount`. The ERC20 branch correctly adds `amount` to the current total before comparing.

Consequence: when `totalAssetDeposits == depositLimit` (or any value below it), the ETH check returns `false` and the deposit is accepted, even if `amount` is arbitrarily large. After the deposit, `getTotalAssetDeposits(ETH)` will exceed `depositLimitByAsset(ETH)` by the full deposit amount.

This is called from `_beforeDeposit`, which is the sole guard in `depositETH`:

```solidity
function depositETH(...) external payable ... {
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    ...
}
``` [2](#0-1) [3](#0-2) 

---

### Impact Explanation

The ETH deposit limit (`depositLimitByAsset`) is the protocol's primary risk-management cap on ETH exposure. Any unprivileged depositor can push the protocol's ETH holdings above this cap in a single transaction. The protocol will mint rsETH against ETH that exceeds the intended ceiling, violating the invariant that total ETH deposits ≤ configured limit.

**Impact class**: Low — Contract fails to deliver promised returns (the deposit cap), but no direct fund loss occurs.

---

### Likelihood Explanation

The entry path is `depositETH`, which is public and callable by any user with ETH. No special role, front-running, or oracle manipulation is required. The condition is triggered whenever `totalAssetDeposits` is at or below the limit and a depositor sends any nonzero ETH. This is a routine state during normal protocol operation.

---

### Recommendation

Add `amount` to the ETH branch, matching the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [4](#0-3) 

---

### Proof of Concept

1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 100 ether`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` returns `99 ether`.
3. Attacker calls `depositETH{value: 50 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 50 ether)` evaluates `99 ether > 100 ether` → `false` → limit not exceeded.
5. Deposit proceeds; protocol now holds 149 ETH, 49 ETH above the configured cap.
6. rsETH is minted for the full 50 ETH deposit with no revert.

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
