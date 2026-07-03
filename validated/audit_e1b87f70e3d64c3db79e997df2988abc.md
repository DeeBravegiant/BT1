Audit Report

## Title
Strict FIFO `break` in `_unlockWithdrawalRequests` Allows Any Large Head-of-Queue Request to Block All Subsequent Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`_unlockWithdrawalRequests` processes withdrawal requests in strict FIFO order and hard-`break`s the entire loop when the head-of-queue request's payout exceeds the vault's current liquid balance. Because `nextLockedNonce` is only advanced for successfully processed requests, all subsequent requests — including arbitrarily small ones that could be fully satisfied — are permanently frozen until the blocking request is funded. Any unprivileged rsETH holder can trigger this condition by queuing a large withdrawal when total protocol deposits are high but the vault's liquid balance is low.

## Finding Description
`_unlockWithdrawalRequests` iterates from `nextLockedNonce[asset]` upward:

```solidity
// L790–815
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    if (availableAssetAmount < payoutAmount) break; // hard stop
    ...
    nextLockedNonce_++;
}
nextLockedNonce[asset] = nextLockedNonce_;
``` [1](#0-0) 

When the `break` fires, `nextLockedNonce_` is unchanged and written back to storage at line 815, leaving the blocking request permanently at the head. There is no `continue`, skip, or requeue path.

`completeWithdrawal` enforces:

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
``` [2](#0-1) 

So any user whose nonce is ≥ the stalled `nextLockedNonce` cannot complete their withdrawal.

The structural gap enabling this: `initiateWithdrawal` validates against `getAvailableAssetAmount`, which uses total protocol deposits minus committed assets: [3](#0-2) 

But `_createUnlockParams` supplies `unstakingVault.balanceOf(asset)` — only the liquid balance already unstaked from EigenLayer — as `availableAssetAmount`: [4](#0-3) 

A large withdrawal can always be legitimately queued when TVL is high, yet the vault's liquid balance is routinely far lower because most assets remain staked in EigenLayer. This is a normal operating condition, not an edge case.

## Impact Explanation
Users whose requests are queued behind a large blocking request cannot call `completeWithdrawal` — their rsETH was already transferred to the contract at `initiateWithdrawal` time and is locked with no recourse until the vault accumulates enough assets to satisfy the head request first. This constitutes **temporary freezing of funds**, a valid Medium impact per the allowed scope.

## Likelihood Explanation
`initiateWithdrawal` is a public, permissionless function callable by any rsETH holder. The availability check at initiation time uses total deposits (not liquid vault balance), so a large request can always be queued when the protocol has significant TVL. Because EigenLayer unstaking has a multi-day unbonding period, the vault's liquid balance is structurally lower than total deposits during normal operation. No privileged access, oracle manipulation, or unrealistic assumptions are required.

## Recommendation
Replace the hard `break` on insufficient assets with a `continue` so the loop skips underfunded requests and processes smaller ones behind them. Alternatively, introduce an operator-callable "advance nonce" function that moves `nextLockedNonce` past a provably underfunded request without permanently blocking it, or allow `firstExcludedIndex` to specify a non-contiguous set of nonces to unlock.

## Proof of Concept
1. Protocol has 2,000 ETH total deposits; `unstakingVault.balanceOf(ETH)` = 500 ETH.
2. **Alice** calls `initiateWithdrawal(ETH, rsETH_for_1000_ETH)`. Check: `1000 < 2000 - 0` ✓. Assigned nonce 0. `assetsCommitted[ETH] = 1000`.
3. **Bob** calls `initiateWithdrawal(ETH, rsETH_for_1_ETH)`. Check: `1 < 2000 - 1000` ✓. Assigned nonce 1. `assetsCommitted[ETH] = 1001`.
4. Operator calls `unlockQueue(ETH, 2, ...)`. `_createUnlockParams` returns `totalAvailableAssets = 500`.
5. Loop, nonce 0: `payoutAmount ≈ 1000 ETH`, `500 < 1000` → **`break`**. `nextLockedNonce[ETH]` stays at 0.
6. Bob calls `completeWithdrawal(ETH)`: `usersFirstWithdrawalRequestNonce = 1`, `nextLockedNonce[ETH] = 0`, `1 >= 0` → **`revert WithdrawalLocked()`**.
7. Bob's 1 ETH rsETH is frozen in the contract until the vault accumulates ≥ 1,000 ETH to unblock Alice's request.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L707-707)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L800-815)
```text
            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```
