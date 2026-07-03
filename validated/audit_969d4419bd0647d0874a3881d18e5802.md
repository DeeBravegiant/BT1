Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing Amount in Limit Check — (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses two different comparison expressions depending on asset type. The ETH branch omits the incoming `amount` from the comparison, so the deposit limit is never enforced against the size of the current ETH deposit — only against the pre-existing total. Any unprivileged depositor can push ETH holdings arbitrarily beyond the configured cap in a single transaction.

## Finding Description
At [1](#0-0)  the function branches on asset type:

- **ETH branch (L679):** `return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));` — `amount` is absent.
- **ERC-20 branch (L681):** `return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));` — `amount` is correctly included.

When `totalAssetDeposits ≤ limit`, the ETH branch unconditionally returns `false` regardless of `amount`, so `_beforeDeposit` never reverts via `MaximumDepositLimitReached` for ETH. [2](#0-1) 

The exploit path is fully permissionless: `depositETH` is a public `payable` function with no role restriction, calling `_beforeDeposit` as its sole pre-flight check. [3](#0-2) 

An additional inconsistency exists: `getAssetCurrentLimit` correctly uses `>` and reports `0` remaining capacity when `totalAssetDeposits == limit`, while the enforcement gate still allows the deposit through. [4](#0-3) 

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

The deposit limit is a risk-management parameter set by the admin to cap protocol exposure to ETH. Because the cap is unenforceable, a single depositor can mint rsETH for an amount far exceeding the remaining capacity. The protocol then holds more ETH than it was designed to manage, excess ETH sits idle in the deposit pool diluting effective yield backing rsETH, and the protocol's stated deposit constraints are violated — without any loss of principal.

## Likelihood Explanation
**High.** The vulnerable path is the primary, permissionless ETH deposit function. No special role, timing, or market condition is required. Any depositor who observes that `totalAssetDeposits ≤ limit` can exploit this in a single transaction. The discrepancy is structural and present in every deployment.

## Recommendation
Apply the same expression used for ERC-20 assets to the ETH branch, removing the special-case entirely:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
-   if (asset == LRTConstants.ETH_TOKEN) {
-       return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
-   }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
Assume `depositLimitByAsset(ETH_TOKEN) = 1000 ETH` and `getTotalAssetDeposits(ETH_TOKEN) = 999 ETH`.

1. Alice calls `depositETH{value: 500 ETH}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500 ETH)`.
3. ETH branch evaluates `999 ETH > 1000 ETH` → `false` → limit not exceeded, no revert.
4. `_mintRsETH` mints rsETH for the full 500 ETH.
5. `getTotalAssetDeposits(ETH_TOKEN)` is now `1499 ETH` — 49.9% above the configured cap.

The same call with any ERC-20 LST (`totalDeposits = 999`, `amount = 500`) evaluates `999 + 500 > 1000` → `true` → `MaximumDepositLimitReached` revert, confirming the inconsistency.

**Foundry test plan:**
```solidity
function test_ETHDepositBypassesLimit() public {
    // Set ETH deposit limit to 1000 ether
    lrtConfig.setDepositLimitByAsset(LRTConstants.ETH_TOKEN, 1000 ether);
    // Seed pool so totalAssetDeposits = 999 ether
    vm.deal(address(lrtDepositPool), 999 ether);
    // Alice deposits 500 ether — should revert but does not
    vm.deal(alice, 500 ether);
    vm.prank(alice);
    lrtDepositPool.depositETH{value: 500 ether}(0, "");
    // Assert total now exceeds limit
    assertGt(lrtDepositPool.getTotalAssetDeposits(LRTConstants.ETH_TOKEN), 1000 ether);
}
```

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

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
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
