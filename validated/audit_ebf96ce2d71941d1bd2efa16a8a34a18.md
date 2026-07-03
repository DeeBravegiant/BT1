The code confirms the claim exactly as described. At [1](#0-0)  the ETH branch evaluates `totalAssetDeposits > limit` (missing `+ amount`), while the ERC-20 branch correctly evaluates `totalAssetDeposits + amount > limit`. The call chain through `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` is confirmed at [2](#0-1) .

---

Audit Report

## Title
ETH Deposit Limit Check Omits New Deposit Amount, Allowing Limit to Be Exceeded - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric ceiling check: the ETH branch evaluates `totalAssetDeposits > limit` without adding the incoming `amount`, while the ERC-20 branch correctly evaluates `totalAssetDeposits + amount > limit`. When total ETH deposits exactly equal the configured limit, the guard returns `false` and `depositETH` proceeds to mint rsETH, pushing total deposits above the intended ceiling.

## Finding Description
In `contracts/LRTDepositPool.sol` at L676–682, the function `_checkIfDepositAmountExceedesCurrentLimit` contains two branches. The ETH branch (L679) returns `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)`, omitting the incoming `amount`. The ERC-20 branch (L681) returns `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`, which is correct. When `totalAssetDeposits == depositLimitByAsset(ETH)`, the ETH branch evaluates `limit > limit` → `false`, so `_beforeDeposit` (L661) does not revert with `MaximumDepositLimitReached`. Control returns to `depositETH` (L87–90), which calls `_mintRsETH`, minting rsETH for the full `msg.value` and leaving total ETH deposits above the configured ceiling. The ERC-20 equivalent path would have reverted (`limit + amount > limit` → `true`).

## Impact Explanation
The deposit limit (`depositLimitByAsset`) is the protocol's primary risk-management ceiling for ETH exposure. Bypassing it causes rsETH to be minted against ETH the protocol did not intend to accept, violating the protocol's own accounting invariant. This matches the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value"** — the ceiling is not enforced, but no direct theft of existing funds occurs in a single transaction.

## Likelihood Explanation
Any unprivileged depositor can trigger this with a standard `depositETH{value: X}()` call. The boundary condition `totalAssetDeposits == depositLimitByAsset(ETH)` is a natural state that occurs whenever the pool approaches capacity. No special permissions, flash loans, oracle manipulation, or victim mistakes are required.

## Recommendation
Add the incoming `amount` to the ETH branch, mirroring the ERC-20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 1000 ether`.
2. Protocol accumulates `totalAssetDeposits(ETH) = 1000 ether` through normal usage.
3. Attacker calls `depositETH{value: 1 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `1000 ether > 1000 ether` → `false` → no revert.
5. `_mintRsETH` mints rsETH for 1 ETH; total ETH deposits become 1001 ether, exceeding the configured ceiling.
6. The ERC-20 equivalent path would have reverted at step 4 (`1000 + 1 > 1000` → `true`).

**Foundry test plan:** Deploy `LRTDepositPool` with a mock `lrtConfig` returning `depositLimitByAsset(ETH) = 1000 ether`. Seed the pool so `getTotalAssetDeposits(ETH) == 1000 ether`. Call `depositETH{value: 1 ether}(0, "")` from an unprivileged address. Assert the call succeeds (no revert) and `getTotalAssetDeposits(ETH) == 1001 ether`, confirming the ceiling was breached.

### Citations

**File:** contracts/LRTDepositPool.sol (L87-90)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);
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
