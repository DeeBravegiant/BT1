Audit Report

## Title
ETH Deposit Cap Bypass Due to Missing `amount` in `_checkIfDepositAmountExceedesCurrentLimit` - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric bounds check: the ERC-20 branch correctly includes the incoming `amount` in the comparison (`totalAssetDeposits + amount > limit`), but the ETH branch omits it (`totalAssetDeposits > limit`). Any unprivileged caller can invoke `depositETH` with an arbitrarily large value and push the protocol's ETH holdings above the configured `depositLimitByAsset` cap, violating the protocol's core deposit invariant.

## Finding Description
The function at [1](#0-0)  is the sole enforcement gate for the per-asset deposit cap. For ETH, line 679 evaluates only `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)`, completely ignoring the incoming `amount`. For ERC-20 tokens, line 681 correctly evaluates `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`.

This function is called unconditionally from `_beforeDeposit` at [2](#0-1) , which is invoked by the public entry point `depositETH` at [3](#0-2) .

No other guard exists for the ETH cap. As long as `totalAssetDeposits <= depositLimitByAsset(ETH)` at the time of the call, the check returns `false` regardless of `amount`, and rsETH is minted for the full deposit. The `getAssetCurrentLimit` view at [4](#0-3)  will subsequently return 0 once the limit is exceeded, giving integrators incorrect data.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The `depositLimitByAsset` cap for ETH is the protocol's primary safety bound on native ETH intake. Bypassing it means the protocol mints rsETH and accepts ETH beyond the intended ceiling, violating the invariant `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset(ETH)`. No direct fund theft or freeze occurs; the excess ETH is restaked normally. The impact is that the protocol does not enforce the cap it promises to enforce for ETH, while correctly enforcing it for all LSTs.

## Likelihood Explanation
**High.** `depositETH` is a public, permissionless, payable function. The only modifiers are `nonReentrant`, `whenNotPaused`, and `onlySupportedAsset(ETH_TOKEN)` — none of which restrict who can call it or how much ETH they can send. No role, whitelist, or additional condition stands between a depositor and the flawed check. The exploit requires no special timing, front-running, or privileged access.

## Recommendation
Apply the same combined check used for ERC-20 tokens to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This unifies the check for both ETH and ERC-20 assets, ensuring the incoming `amount` is always included in the boundary comparison.

## Proof of Concept
Assume `depositLimitByAsset(ETH) = 100 ether` and `getTotalAssetDeposits(ETH) = 99 ether`.

1. Attacker calls `depositETH{value: 500 ether}(0, "")`.
2. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 500 ether)` evaluates: `99 ether > 100 ether` → `false` → limit not exceeded.
3. `_beforeDeposit` does not revert; rsETH is minted for 500 ETH.
4. `getTotalAssetDeposits(ETH)` is now 599 ETH — nearly 6× the intended cap.

For comparison, the same attempt with stETH (`depositAmount = 500 ether`) evaluates `99 + 500 > 100` → `true` → `MaximumDepositLimitReached` revert, confirming the asymmetry.

**Foundry test sketch:**
```solidity
function test_ethCapBypass() public {
    // set ETH deposit limit to 100 ether
    lrtConfig.setDepositLimitByAsset(LRTConstants.ETH_TOKEN, 100 ether);
    // deposit 99 ether to approach the cap
    depositor.depositETH{value: 99 ether}(0, "");
    // attempt to deposit 500 ether — should revert but does not
    depositor.depositETH{value: 500 ether}(0, ""); // passes incorrectly
    assertGt(lrtDepositPool.getTotalAssetDeposits(LRTConstants.ETH_TOKEN), 100 ether);
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
