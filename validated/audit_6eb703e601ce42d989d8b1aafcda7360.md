Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing `amount` in ETH Branch Limit Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` omits the incoming `amount` (equal to `msg.value`) from the ETH branch comparison, checking only `totalAssetDeposits > limit` instead of `totalAssetDeposits + amount > limit`. This allows any single `depositETH` call to push total ETH holdings arbitrarily above the configured `depositLimitByAsset` cap, while the ERC20 branch correctly includes `amount`.

## Finding Description
In `contracts/LRTDepositPool.sol` at L676–682, the function has an asymmetric guard:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // amount silently dropped
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
}
```

The call chain is: `depositETH` (L87) passes `msg.value` as `depositAmount` to `_beforeDeposit` (L661), which passes it as `amount` to `_checkIfDepositAmountExceedesCurrentLimit`. In the ETH branch, `amount` is never added to `totalAssetDeposits` before the comparison, so the check only verifies whether the pre-deposit total already exceeds the limit — not whether the new deposit would push it over. Existing guards (`nonReentrant`, `whenNotPaused`, `onlySupportedAsset`) do not compensate for this logic error.

## Impact Explanation
The `depositLimitByAsset` cap is the sole on-chain mechanism preventing the protocol from accepting more ETH than intended. Because the ETH branch never adds the incoming deposit to the running total, the cap is never enforced for ETH: a depositor can send an arbitrarily large `msg.value` in a single transaction as long as `totalAssetDeposits` has not yet crossed the limit. The excess ETH is minted as rsETH and held in the deposit pool or forwarded to NodeDelegators, but EigenLayer pod/strategy capacity may not accommodate the overflow, leaving ETH unable to be staked — a **temporary freezing of funds** (Medium per allowed scope). Additionally, inflating ETH TVL beyond the cap distorts the rsETH exchange rate for all existing holders.

## Likelihood Explanation
`depositETH` is public, payable, and requires no special role. Any depositor can observe `getAssetCurrentLimit(ETH_TOKEN)` on-chain to confirm the remaining headroom, then send a single transaction with `msg.value` far exceeding it. No flash loan, no privilege, and no splitting is required. The condition is trivially detectable and repeatable.

## Recommendation
Mirror the ERC20 branch in the ETH branch by adding `amount` to `totalAssetDeposits`:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
Assume `depositLimitByAsset[ETH] = 100 ether` and `getTotalAssetDeposits(ETH) = 99 ether`.

1. Attacker calls `depositETH{value: 1000 ether}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 1000 ether)`.
3. ETH branch evaluates `99 ether > 100 ether` → `false` → limit not exceeded, no revert.
4. `_mintRsETH` mints rsETH for 1000 ETH; total ETH in protocol becomes 1099 ETH, 999 ETH above the cap.
5. Excess ETH cannot be deployed to EigenLayer if pod/strategy capacity is exhausted, leaving it frozen.

**Foundry test plan**: Deploy `LRTDepositPool` on a fork, set `depositLimitByAsset[ETH] = 100 ether`, seed 99 ETH via a prior deposit, then call `depositETH{value: 1000 ether}` from an unprivileged address and assert it does not revert and that `getTotalAssetDeposits(ETH)` returns 1099 ether — confirming the cap is bypassed.