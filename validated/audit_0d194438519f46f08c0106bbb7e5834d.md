Audit Report

## Title
ETH Deposit Branch Omits `amount` from Limit Check, Allowing Cap to Be Exceeded - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses two different comparison expressions depending on the asset. The ERC-20 branch correctly evaluates `totalAssetDeposits + amount > limit`, but the ETH branch evaluates only `totalAssetDeposits > limit`, silently dropping the incoming `amount`. As a result, any ETH deposit that would push the running total above the configured cap is accepted and rsETH is minted, violating the protocol's deposit ceiling.

## Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` (L676–682) contains an asset-specific branch:

```solidity
// L678-681
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount ignored
}
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
``` [1](#0-0) 

This function is the sole pre-deposit guard. It is called unconditionally from `_beforeDeposit`:

```solidity
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [2](#0-1) 

`_beforeDeposit` is invoked by `depositETH` with `msg.value` as the deposit amount:

```solidity
uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
``` [3](#0-2) 

Because the ETH branch never adds `amount` to `totalAssetDeposits`, the guard only fires when the cap is **already** exceeded. Any deposit that would push the total from below the cap to above it passes the check, and rsETH is minted for the full `msg.value`.

## Impact Explanation
The ETH deposit cap (`depositLimitByAsset[ETH_TOKEN]`) is the protocol's primary mechanism for bounding total ETH exposure. The bypass allows the protocol to accept and mint rsETH against more ETH than governance has authorised, violating the promised deposit ceiling. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.** Deposited ETH is not stolen; the harm is that the supply cap is not enforced.

## Likelihood Explanation
The entry point (`depositETH`) is fully permissionless — no role, no front-running, and no external dependency is required. The vulnerable condition (`totalAssetDeposits < limit`) is the normal operating state of the protocol for most of its lifetime. Any depositor can trigger it in a single transaction whenever the running total is within one deposit of the cap.

## Recommendation
Remove the ETH-specific branch entirely and apply the same expression used for ERC-20 assets:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The special-casing of `ETH_TOKEN` is the root cause and is unnecessary.

## Proof of Concept
Setup:
- `depositLimitByAsset[ETH_TOKEN] = 100 ether`
- `getTotalAssetDeposits(ETH_TOKEN)` returns `99 ether`

**ERC-20 path (correct):** `99 + 10 = 109 > 100` → reverts with `MaximumDepositLimitReached`.

**ETH path (buggy):** `99 > 100` → `false` → no revert → 10 ETH accepted, rsETH minted, total becomes 109 ETH — 9 ETH above the cap.

Foundry test plan:
1. Deploy `LRTDepositPool` with a mock `LRTConfig` setting `depositLimitByAsset[ETH_TOKEN] = 100 ether`.
2. Seed the pool so `getTotalAssetDeposits(ETH_TOKEN)` returns `99 ether`.
3. Call `depositETH{value: 10 ether}(0, "")` from an unprivileged address.
4. Assert the call does **not** revert and `getTotalAssetDeposits(ETH_TOKEN)` returns `109 ether`, confirming the cap was breached.

### Citations

**File:** contracts/LRTDepositPool.sol (L87-87)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
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
