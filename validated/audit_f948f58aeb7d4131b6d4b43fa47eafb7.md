Audit Report

## Title
ETH Deposit Limit Bypass via Missing Amount in Limit Check — (`contracts/LRTDepositPool.sol`)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric comparison: for ERC-20 tokens it checks `totalAssetDeposits + amount > limit`, but for ETH it checks only `totalAssetDeposits > limit`, omitting the incoming deposit amount. When the running ETH total equals the configured limit exactly, the check returns `false` and the deposit is accepted, pushing the protocol above its own cap. Any unprivileged depositor can engineer this condition in a single atomic transaction.

## Finding Description
At `contracts/LRTDepositPool.sol` lines 676–682, the function reads:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // amount included
}
```

When `totalAssetDeposits == depositLimit`, the ETH branch evaluates `depositLimit > depositLimit` → `false`, so `_beforeDeposit` (lines 648–670) does not revert with `MaximumDepositLimitReached`, and `_mintRsETH` issues rsETH for the full over-limit deposit. `getTotalAssetDeposits(ETH)` aggregates `address(this).balance` and all downstream balances, which increase immediately upon deposit, so the state is consistent with the described exploit. The public view `getAssetCurrentLimit` (lines 402–409) correctly reports `0` remaining capacity at this state, creating a discrepancy between what off-chain tooling reports and what the contract actually enforces.

## Impact Explanation
The deposit limit is the protocol's primary on-chain risk-management gate for ETH exposure. Bypassing it allows rsETH to be minted against ETH the protocol never intended to hold, expanding protocol risk beyond the configured ceiling. This constitutes **Low: contract fails to deliver promised returns** — the protocol's advertised deposit cap is not enforced. If the limit was sized to bound EigenLayer slashing exposure, repeated bypass could escalate toward protocol insolvency.

## Likelihood Explanation
No privileged role, oracle manipulation, or external dependency is required. Any depositor can:
1. Read `depositLimit` and `getTotalAssetDeposits(ETH)` to compute the gap `G`.
2. In one transaction via a helper contract: deposit `G` ETH (bringing the total to exactly `depositLimit`), then immediately deposit any additional amount `Y`.
3. On the second call, `totalAssetDeposits == depositLimit`, the check passes, and `Y` ETH worth of rsETH is minted beyond the cap.

The only precondition is `G ≥ minAmountToDeposit`, which is trivially satisfiable whenever the limit has not yet been fully reached.

## Recommendation
Apply the same inclusive comparison for ETH that is already used for ERC-20 tokens:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This makes the ETH path consistent with the ERC-20 path and closes the bypass.

## Proof of Concept
```
State: depositLimit = 1000 ETH, getTotalAssetDeposits(ETH) = 999 ETH

Attacker contract, single transaction:
  Step 1 — depositETH{value: 1 ETH}(0, "")
    _checkIfDepositAmountExceedesCurrentLimit:
      totalAssetDeposits = 999 ETH
      999 > 1000  →  false  →  deposit accepted
    After: totalAssetDeposits = 1000 ETH (exactly at limit)

  Step 2 — depositETH{value: 500 ETH}(0, "")
    _checkIfDepositAmountExceedesCurrentLimit:
      totalAssetDeposits = 1000 ETH
      1000 > 1000  →  false  →  deposit accepted  ← BYPASS
    rsETH minted for 500 ETH at current oracle rate
    After: totalAssetDeposits = 1500 ETH (50% over limit)
```

Foundry invariant test: assert that after any sequence of `depositETH` calls, `getTotalAssetDeposits(ETH_TOKEN) <= lrtConfig.depositLimitByAsset(ETH_TOKEN)`. This invariant will be violated by the two-step sequence above.