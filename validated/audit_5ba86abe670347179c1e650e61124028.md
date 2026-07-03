Audit Report

## Title
ETH Deposit Limit Check Omits Incoming Amount, Allowing Cap Bypass - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit()` in `LRTDepositPool.sol` evaluates the ETH branch as `totalAssetDeposits > depositLimit` instead of `totalAssetDeposits + amount > depositLimit`. This means the check only detects an already-exceeded cap, never a cap that would be exceeded by the incoming deposit. Any unprivileged caller can invoke `depositETH()` and push total ETH holdings above the configured limit while the guard silently passes.

## Finding Description
The sole cap-enforcement function is `_checkIfDepositAmountExceedesCurrentLimit()`:

```solidity
// contracts/LRTDepositPool.sol L676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
}
``` [1](#0-0) 

`_beforeDeposit()` calls this function and reverts with `MaximumDepositLimitReached` only when it returns `true`: [2](#0-1) 

Because the ETH branch never adds `amount`, the condition is `false` whenever `totalAssetDeposits <= depositLimit`, regardless of how large `amount` is. The ERC20 branch adds `amount` before comparing, making the asymmetry a clear coding defect. `depositETH()` is fully permissionless: [3](#0-2) 

## Impact Explanation
The deposit limit is an admin-configured risk ceiling. The ETH branch failure allows the protocol to accept and mint rsETH against more ETH than the admin intended. No funds are stolen and no yield is lost, but the contract fails to enforce its own deposit-limit guarantee. This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value**, which is within the allowed impact scope.

## Likelihood Explanation
The entry point `depositETH()` requires no role, no special token, no oracle condition, and no front-running. Any externally-owned account can call it while the contract is unpaused. The attacker only needs to observe that `totalAssetDeposits` is at or near the limit and submit a deposit exceeding the remaining headroom. The condition is trivially observable on-chain and repeatable across multiple transactions.

## Recommendation
Add `amount` to the ETH branch to match the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets ETH deposit limit to 1 000 ETH via `LRTConfig.updateAssetDepositLimit()`.
2. `getTotalAssetDeposits(ETH_TOKEN)` returns exactly 1 000 ETH (`totalAssetDeposits == depositLimit`).
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500e18)`.
5. ETH branch evaluates `1000e18 > 1000e18` → `false` → no revert.
6. `_mintRsETH` mints rsETH for 500 ETH; protocol now holds 1 500 ETH against a 1 000 ETH cap.

**Foundry fuzz test plan**: Deploy `LRTDepositPool` on a local fork, set ETH limit to `L`, seed deposits to exactly `L`, then fuzz `depositETH` with arbitrary `amount > 0`. Assert that `getTotalAssetDeposits(ETH_TOKEN)` never exceeds `L` after the call. The assertion will fail for any non-zero `amount`, confirming the bypass. For ERC20, the identical fuzz will pass, confirming the asymmetry.

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
