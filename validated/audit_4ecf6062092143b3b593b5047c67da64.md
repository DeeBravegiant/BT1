The code at lines 676-682 of `contracts/LRTDepositPool.sol` exactly matches the claim. The ETH branch omits `amount` from the comparison while the ERC20 branch correctly includes it. All 7 required validation checks pass. The exploit path is permissionless, the root cause is confirmed in the actual source, and the impact (deposit cap bypass → rsETH minted beyond ceiling) fits the allowed "Low" scope.

---

Audit Report

## Title
Ineffective ETH Deposit Limit Check Allows Depositors to Exceed Protocol Cap - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol` omits the incoming `amount` from the ETH branch comparison, making the cap check evaluate only whether the current total already exceeds the limit rather than whether the deposit would push it over. Any unprivileged caller can deposit an arbitrarily large amount of ETH as long as `totalAssetDeposits <= depositLimitByAsset(ETH)` at call time, causing the protocol to mint rsETH beyond its configured ceiling.

## Finding Description
At lines 676–682 of `contracts/LRTDepositPool.sol`, the function reads:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
}
```

The ERC20 branch correctly evaluates `totalAssetDeposits + amount > limit`. The ETH branch evaluates only `totalAssetDeposits > limit`, ignoring `amount` entirely. During normal protocol operation (`totalAssetDeposits <= limit`), this expression is always `false`, so the function always returns `false` for ETH regardless of how large `amount` is.

This function is called at line 661 from `_beforeDeposit`, which gates every `depositETH` call (lines 76–93). Because the guard never returns `true` while the protocol is under its cap, `MaximumDepositLimitReached` is never reverted for ETH deposits, and `_mintRsETH` executes unconditionally. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation
The ETH deposit cap is a protocol-level invariant. Because the guard is ineffective, any depositor can mint rsETH beyond the configured ceiling. No funds are stolen and no yield is diverted, but the protocol fails to deliver its promised deposit limit guarantee. This maps exactly to the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value."**

## Likelihood Explanation
`depositETH` is a public, permissionless, payable function. No special role, flash loan, oracle dependency, or front-running is required. The vulnerable condition (`totalAssetDeposits <= depositLimit`) is the normal operating state of the protocol. Any depositor can trigger this on every call, making likelihood **High**.

## Recommendation
Add `amount` to the ETH branch to match the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [4](#0-3) 

## Proof of Concept

1. Deploy/fork with `depositLimitByAsset(ETH) = 1000 ether` and `getTotalAssetDeposits(ETH) = 999 ether` (normal operating state, under cap).
2. Any EOA calls `depositETH{value: 500 ether}(0, "")`.
3. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 500 ether)` evaluates `999 ether > 1000 ether` → `false`.
4. `_beforeDeposit` does not revert; `_mintRsETH` executes and mints rsETH for 500 ETH.
5. `getTotalAssetDeposits(ETH)` is now `1499 ether`, ~50% above the configured cap.
6. The same call can be repeated by any depositor in the same block with no bound on overshoot.

Foundry test plan: set `depositLimitByAsset` to `1000 ether`, warp/deal to put `totalAssetDeposits` at `999 ether`, call `depositETH{value: 500 ether}`, assert the call succeeds and `getTotalAssetDeposits(ETH) > depositLimitByAsset(ETH)`.

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
