Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing `amount` in Cumulative Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` omits the incoming `amount` from the ETH branch of the deposit cap check, evaluating only `totalAssetDeposits > limit` instead of `totalAssetDeposits + amount > limit`. Any unprivileged caller can invoke `depositETH` with an arbitrarily large `msg.value` and exceed the configured ETH deposit cap in a single transaction, while the equivalent LST path correctly enforces the cumulative check.

## Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` (L676–682) contains an asymmetric guard:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
}
```

`_beforeDeposit` (L648–670) calls this function and reverts with `MaximumDepositLimitReached` only when it returns `true`. For ETH, the function returns `true` only when the cap is **already** exceeded before the deposit, so any deposit that arrives while `totalAssetDeposits ≤ limit` passes regardless of its size. `depositETH` (L76–93) passes `msg.value` as `depositAmount` to `_beforeDeposit`, but that value is never incorporated into the ETH limit comparison.

Exploit path:
1. `depositLimitByAsset(ETH_TOKEN) = 1000 ether`, `totalAssetDeposits(ETH_TOKEN) = 999 ether`.
2. Attacker calls `depositETH{value: 500 ether}(0, "")`.
3. Check evaluates `999 ether > 1000 ether` → `false` → no revert.
4. 500 ETH is accepted; total becomes 1499 ETH — 49.9% above the cap.
5. The same call with an LST evaluates `999 + 500 > 1000` → `true` → correctly reverts.

No existing guard compensates: `whenNotPaused` and `onlySupportedAsset` are orthogonal to the limit logic, and `nonReentrant` does not affect the arithmetic.

## Impact Explanation
The deposit limit bounds protocol exposure to EigenLayer slashing risk. Bypassing it allows unbounded ETH to be deposited into EigenLayer strategies in a single permissionless call, directly threatening protocol solvency if slashing events occur against the excess position. This maps to **Critical — Protocol Insolvency** within the allowed impact scope.

## Likelihood Explanation
`depositETH` is fully permissionless (no role restriction beyond `whenNotPaused`). The precondition — `totalAssetDeposits ≤ depositLimit` — is the normal operating state of the protocol. Any depositor can trigger this at any time without coordination, special timing, or privileged access. The attack is repeatable across multiple transactions.

## Recommendation
Add `amount` to the ETH branch to match the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
Foundry unit test outline:

```solidity
function test_ethDepositExceedsLimit() public {
    uint256 limit = 1000 ether;
    vm.prank(admin);
    lrtConfig.setDepositLimitByAsset(LRTConstants.ETH_TOKEN, limit);

    // Bring total to 999 ether via a legitimate deposit
    vm.deal(alice, 999 ether);
    vm.prank(alice);
    lrtDepositPool.depositETH{value: 999 ether}(0, "");

    // Single deposit of 500 ether should revert but does not
    vm.deal(attacker, 500 ether);
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 500 ether}(0, ""); // expect no revert

    uint256 total = lrtDepositPool.getTotalAssetDeposits(LRTConstants.ETH_TOKEN);
    assertGt(total, limit); // 1499 ether > 1000 ether — cap breached
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** contracts/LRTDepositPool.sol (L648-663)
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
