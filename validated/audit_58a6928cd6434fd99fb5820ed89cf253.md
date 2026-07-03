### Title
ETH Deposit Cap Not Properly Enforced Due to Missing Amount in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` uses an asymmetric comparison for ETH vs. LST assets. The ETH branch omits the incoming deposit `amount` from the cap check, meaning the limit is only enforced after it has already been exceeded — not before. Any unprivileged depositor can deposit an arbitrarily large amount of ETH in a single call as long as the current total is at or below the cap.

### Finding Description
In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← missing `+ amount`
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

For LST assets the check is `totalAssetDeposits + amount > depositLimit` — it correctly accounts for the incoming deposit. For ETH the check is `totalAssetDeposits > depositLimit` — it only asks whether the limit was already exceeded *before* this deposit, never whether *this* deposit would exceed it.

Consequence: while `totalAssetDeposits ≤ depositLimit`, the function always returns `false` (not exceeded) regardless of `amount`. A depositor can therefore send any quantity of ETH in a single `depositETH` call and the cap will not fire. After the deposit `totalAssetDeposits` jumps above the limit, blocking all subsequent deposits — but the damage is already done.

### Impact Explanation
The ETH deposit cap (`depositLimitByAsset[ETH_TOKEN]`) is a TVL risk-management parameter. Because it is never checked against the incoming amount, a single depositor can push total ETH deposits arbitrarily above the configured limit in one transaction. This causes `_mintRsETH` to mint rsETH far in excess of what the cap was designed to allow, breaking the protocol's TVL invariant and potentially leading to protocol insolvency (rsETH over-minted relative to the intended backing).

**Impact: Low — Contract fails to deliver promised returns (deposit cap is not enforced as promised); in a worst-case scenario where the cap is the sole guard against over-minting, this escalates toward protocol insolvency.**

### Likelihood Explanation
The entry point is the public, permissionless `depositETH` function. No role, whitelist, or special condition is required. Any depositor who observes that `totalAssetDeposits ≤ depositLimit` can exploit this in a single transaction. Likelihood is **High**.

### Recommendation
Add `amount` to the ETH branch of the check, mirroring the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100 ether` via `LRTConfig.updateAssetDepositLimit`.
2. `getTotalAssetDeposits(ETH_TOKEN)` currently returns `50 ether`.
3. Attacker calls `depositETH{value: 10_000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `50 ether > 100 ether` → `false` → deposit is not blocked.
5. `_mintRsETH` mints rsETH for 10 000 ETH; `totalAssetDeposits` becomes `10 050 ether`, 100× the intended cap.
6. All future `depositETH` calls now revert with `MaximumDepositLimitReached`, but the cap has already been violated by 9 950 ETH. [1](#0-0) [2](#0-1) [3](#0-2)

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
