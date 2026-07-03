### Title
ETH Deposit Limit Check Missing `amount` Parameter Allows Deposit Cap Bypass - (File: `contracts/LRTDepositPool.sol`)

---

### Summary

The `_checkIfDepositAmountExceedesCurrentLimit` function in `LRTDepositPool` applies an inconsistent boundary check for ETH versus ERC20 deposits. The ETH branch omits the incoming `amount` from the comparison, meaning the check validates only the pre-deposit state rather than the post-deposit state. Any unprivileged depositor can push the total ETH held by the protocol arbitrarily beyond the configured deposit cap in a single transaction.

---

### Finding Description

In `_checkIfDepositAmountExceedesCurrentLimit`, the ETH and ERC20 branches use structurally different comparisons:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount absent
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount present
}
``` [1](#0-0) 

For ERC20 assets the guard correctly evaluates `totalAssetDeposits + amount > limit`, blocking any deposit that would breach the cap. For ETH the guard evaluates only `totalAssetDeposits > limit`, which is the state *before* the deposit lands. The incoming `amount` is never added to the left-hand side.

The external report's root cause is structurally identical: a strict-inequality check (`hdr_sz < sz`) that fails to handle the boundary case where the two values are equal (zero-sized payload). Here the boundary failure is that the check never accounts for the size of the incoming deposit, so the boundary is always evaluated one step behind reality.

The check is invoked unconditionally from `_beforeDeposit`, which is called by `depositETH`:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
``` [2](#0-1) 

```solidity
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected)
    private view returns (uint256 rsethAmountToMint)
{
    if (depositAmount == 0 || depositAmount < minAmountToDeposit) revert InvalidAmountToDeposit();
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) revert MaximumDepositLimitReached();
    ...
}
``` [3](#0-2) 

---

### Impact Explanation

The deposit limit is the protocol's primary safety cap on how much ETH can enter the system. It is set by the admin to match EigenLayer strategy capacity and risk parameters. When bypassed:

- The protocol mints rsETH for the excess ETH at the current price (no immediate loss to the depositor).
- The excess ETH accumulates in the deposit pool or NDCs but cannot be staked into EigenLayer strategies that are already at capacity.
- `getTotalAssetDeposits` counts this idle ETH in the rsETH price calculation, so the price numerator grows without a corresponding increase in yield-generating assets.
- All existing rsETH holders receive lower yield growth than the protocol promises, because a portion of the backing ETH is permanently idle.

**Impact: Low — Contract fails to deliver promised returns, but does not lose value.**

---

### Likelihood Explanation

The entry path is fully permissionless. Any externally owned account can call `depositETH` with an arbitrarily large `msg.value`. No role, signature, or prior state manipulation is required. The only precondition is that `totalAssetDeposits <= depositLimit` at the moment of the call, which is the normal operating state of the protocol. Likelihood is **High**.

---

### Recommendation

Add `amount` to the ETH branch so it mirrors the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [4](#0-3) 

---

### Proof of Concept

1. Admin sets the ETH deposit limit to 1 000 ETH via `lrtConfig`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` = 999 ETH (one ETH below the cap).
3. Attacker calls `depositETH{value: 50_000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 > 1000` → `false` → `MaximumDepositLimitReached` is **not** reverted.
5. 50 000 ETH is accepted; `totalAssetDeposits` becomes 50 999 ETH — 50× the intended cap.
6. The 49 999 ETH surplus cannot be staked in EigenLayer (strategy at capacity) and sits idle, suppressing rsETH yield growth for all holders indefinitely.

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
