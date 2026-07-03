Audit Report

## Title
`assetsCommitted` Over-Decremented on Price-Drop Unlock Creates Phantom Withdrawal Capacity - (`contracts/LRTWithdrawalManager.sol`)

## Summary
When rsETH price drops between withdrawal initiation and unlock, `_unlockWithdrawalRequests` reduces `assetsCommitted` by the original (higher) `expectedAssetAmount` while only redeeming the lower `payoutAmount` from the unstaking vault. This inflates `getAvailableAssetAmount` by a phantom amount unbacked by real assets, allowing new withdrawal requests to be initiated that can never be fulfilled, temporarily freezing the initiating users' rsETH.

## Finding Description
**Root cause:** In `_unlockWithdrawalRequests`, line 802 decrements `assetsCommitted` by `request.expectedAssetAmount` (the original commitment at initiation price), while line 807 only adds `payoutAmount` (the lower current-price amount) to `assetAmountToUnlock`, which is what gets redeemed from the vault at line 307.

```
// LRTWithdrawalManager.sol L797-807
uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);
if (availableAssetAmount < payoutAmount) break;
assetsCommitted[asset] -= request.expectedAssetAmount;  // ← original higher amount
request.expectedAssetAmount = payoutAmount;
assetAmountToUnlock += payoutAmount;                    // ← only lower amount redeemed
```

`_calculatePayoutAmount` (L833-834) returns `min(expectedAssetAmount, currentReturn)`, so when price drops, `payoutAmount < request.expectedAssetAmount`.

**Accounting divergence after unlock at lower price:**
- `getTotalAssetDeposits` (L395-396) includes `assetLyingUnstakingVault` (L461), which decreases by `payoutAmount` as assets move to the withdrawal manager.
- `assetsCommitted` decreases by `request.expectedAssetAmount`.
- `getAvailableAssetAmount` (L602) = `totalAssets − assetsCommitted` increases by `(expectedAssetAmount − payoutAmount)`.
- Assets held in the withdrawal manager are **not** counted in `getTotalAssetDeposits`, so this freed capacity is phantom — no real assets back it.

**Exploit path:**
1. User A calls `initiateWithdrawal` at price P1 (high); `assetsCommitted += expectedAssetAmount`.
2. rsETH price drops to P2 < P1, within `pricePercentageLimit` (no pause triggered per L273-282 of `LRTOracle.sol`).
3. Operator calls `unlockQueue`; `assetsCommitted -= expectedAssetAmount` but vault only redeems `payoutAmount`.
4. `getAvailableAssetAmount` now shows phantom surplus of `expectedAssetAmount − payoutAmount`.
5. User B calls `initiateWithdrawal` against this phantom capacity; rsETH transferred to withdrawal manager, `assetsCommitted += newExpectedAmount`.
6. Operator calls `unlockQueue` for User B; unstaking vault has no assets, loop breaks at L800 (`if (availableAssetAmount < payoutAmount) break`).
7. User B's rsETH is frozen in the withdrawal manager with no path to completion until the vault is externally replenished.

**Existing guards are insufficient:** The `pricePercentageLimit` pause only triggers for drops *exceeding* the threshold (L273-274); drops within the threshold silently create phantom capacity. The `minimumRsEthPrice` / `minimumAssetPrice` bounds on `unlockQueue` are operator-supplied and do not prevent the divergence — they only gate which price the unlock executes at.

## Impact Explanation
**Medium — Temporary freezing of funds.** User B's rsETH is transferred to the withdrawal manager at `initiateWithdrawal` time (L166) and is inaccessible until the unstaking vault is replenished. The rsETH is not lost permanently (the vault can be topped up), but the user has no recourse during the freeze period. This matches the allowed impact class "Temporary freezing of funds."

## Likelihood Explanation
rsETH price decreases are explicitly anticipated by the protocol (the `pricePercentageLimit` downside protection exists for this reason). Any small drop in underlying LST value or minor EigenLayer slashing event within the threshold is sufficient. The condition requires no attacker — it is triggered by normal market movement followed by a normal operator `unlockQueue` call. Any unprivileged user can then call `initiateWithdrawal` to consume the phantom capacity. The scenario is repeatable on every price-drop unlock cycle.

## Recommendation
In `_unlockWithdrawalRequests`, decrement `assetsCommitted` by `payoutAmount` (the actual amount redeemed) rather than `request.expectedAssetAmount`:

```solidity
// Before (L802):
assetsCommitted[asset] -= request.expectedAssetAmount;

// After:
assetsCommitted[asset] -= payoutAmount;
```

This keeps `getAvailableAssetAmount` consistent with the actual assets remaining in the protocol after a price-drop unlock.

## Proof of Concept
1. Deploy with stETH price = 1e18, rsETH price P1 = 1.05e18.
2. User A: `initiateWithdrawal(stETH, 100e18 rsETH)` → `expectedAssetAmount = 105e18`, `assetsCommitted[stETH] = 105e18`.
3. rsETH price drops to P2 = 1.00e18 (within threshold, no pause).
4. Operator: `unlockQueue(stETH, ...)` → `payoutAmount = min(105e18, 100e18) = 100e18`; `assetsCommitted[stETH] -= 105e18` → 0; vault redeems 100e18.
5. Assert: `getAvailableAssetAmount(stETH)` = `totalAssets − 0`. `totalAssets` decreased by 100e18 (vault drained), but `assetsCommitted` decreased by 105e18. Net phantom = 5e18.
6. User B: `initiateWithdrawal(stETH, ~4.76e18 rsETH)` → `expectedAssetAmount ≈ 5e18`; rsETH transferred to withdrawal manager; `assetsCommitted[stETH] = 5e18`.
7. Operator: `unlockQueue(stETH, ...)` → vault balance = 0 < `payoutAmount`; loop breaks immediately.
8. Assert: User B's rsETH is held in the withdrawal manager with `unlockedWithdrawalsCount` not incremented — funds frozen.