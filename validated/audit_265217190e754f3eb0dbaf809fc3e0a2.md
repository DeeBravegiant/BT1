### Title
EigenLayer Slashing Causes Withdrawal Queue Desync, Temporarily Freezing User Funds - (`contracts/LRTWithdrawalManager.sol`)

### Summary

When `initiateWithdrawal` is called, the protocol records `expectedAssetAmount` in `assetsCommitted[asset]` based on the oracle price at request time. This committed amount is derived from `getAvailableAssetAmount`, which counts EigenLayer-held assets as available. However, EigenLayer's own documentation (reflected in the `IDelegationManager` interface) explicitly states that queued withdrawals are subject to slashing during the delay period, meaning the vault may receive fewer assets than committed. When `unlockQueue` is later called, it uses only the actual vault balance (`unstakingVault.balanceOf(asset)`), which is less than the committed total, causing the queue to stall and blocking all subsequent user withdrawals.

### Finding Description

**Step 1 — Commitment at initiation time (pre-slash)**

`initiateWithdrawal` computes `expectedAssetAmount` from the current oracle price and checks it against `getAvailableAssetAmount`:

```solidity
// LRTWithdrawalManager.sol:168-173
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` is:

```solidity
// LRTWithdrawalManager.sol:599-603
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
}
```

`getTotalAssetDeposits` aggregates assets across all node delegators, including those still held in EigenLayer strategies — assets that are explicitly subject to slashing.

**Step 2 — EigenLayer slashing reduces actual vault receipts**

The `IDelegationManager` interface explicitly warns:

```solidity
// contracts/external/eigenlayer/interfaces/IDelegationManager.sol:313-316
// Withdrawals are still subject to slashing during the delay period so the amount withdrawn
// on completion may actually be less than what was queued if slashing has occurred in that period.
```

When `NodeDelegator.completeUnstaking` finalizes the EigenLayer withdrawal, it transfers only what EigenLayer actually returns (post-slash) to the vault:

```solidity
// NodeDelegator.sol:392-394
assets[i].safeTransfer(
    address(_getUnstakingVault()), assets[i].balanceOf(address(this)) - balancesBefore[i]
);
```

**Step 3 — Queue stalls at unlock time**

`unlockQueue` uses the actual vault balance, not the committed amount:

```solidity
// LRTWithdrawalManager.sol:846-850
return UnlockParams({
    rsETHPrice: lrtOracle.rsETHPrice(),
    assetPrice: lrtOracle.getAssetPrice(asset),
    totalAvailableAssets: unstakingVault.balanceOf(asset)  // actual post-slash balance
});
```

Inside `_unlockWithdrawalRequests`, the loop breaks as soon as the vault balance is insufficient:

```solidity
// LRTWithdrawalManager.sol:800
if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```

Because the queue is FIFO (nonce-ordered), a single under-funded request blocks all requests behind it. `assetsCommitted[asset]` for the stalled requests is never decremented, so `getAvailableAssetAmount` remains artificially low, also preventing new `initiateWithdrawal` calls.

### Impact Explanation

**Temporary freezing of funds.** After EigenLayer slashing, the `LRTWithdrawalManager` withdrawal queue stalls at the first request whose `payoutAmount` exceeds the vault's post-slash balance. All subsequent queued withdrawals are blocked. Users whose rsETH was already burned (transferred to the contract at `initiateWithdrawal`) cannot retrieve their assets until the protocol manually replenishes the vault or the operator intervenes. The `assetsCommitted` desync also prevents new users from initiating withdrawals, compounding the freeze.

### Likelihood Explanation

EigenLayer's new slashing mechanism (v2, reflected in the `IDelegationManager` interface already integrated in this codebase) makes operator slashing a realistic on-chain event. The `LRTUnstakingVault` even imports `SlashingLib`, confirming the protocol is aware of slashing. Any operator slash affecting a node delegator's queued withdrawal during the EigenLayer delay period triggers this condition with no attacker action required — it is a normal protocol risk path.

### Recommendation

In `_unlockWithdrawalRequests`, when the vault balance is insufficient to cover a request's `payoutAmount`, instead of breaking (which blocks the entire queue), either:

1. **Skip and continue** — unlock requests that can be fulfilled, skipping those that cannot (requires non-FIFO ordering or a separate "underfunded" flag).
2. **Cap the payout to available balance** — disburse only what the vault actually holds for that request, similar to the Andromeda recommendation of "only return the available balance when there is insufficient funds."
3. **Decouple `assetsCommitted` from `getTotalAssetDeposits`** — base `getAvailableAssetAmount` only on assets already in the vault, not on EigenLayer-held assets that are still subject to slashing.

### Proof of Concept

1. Protocol has 1000 stETH in EigenLayer via `NodeDelegator`. Oracle prices rsETH at 1:1 with stETH.
2. User calls `initiateWithdrawal(stETH, 100_rsETH)`. `getAvailableAssetAmount` returns 1000 (EigenLayer assets counted). `assetsCommitted[stETH] = 100`.
3. Operator calls `initiateUnstaking` to queue 100 stETH withdrawal from EigenLayer.
4. EigenLayer operator is slashed 20% during the delay period. `completeUnstaking` receives only 80 stETH into the vault.
5. Operator calls `unlockQueue(stETH, ...)`. `_createUnlockParams` returns `totalAvailableAssets = 80` (vault balance).
6. `_unlockWithdrawalRequests` computes `payoutAmount ≈ 100` (oracle hasn't fully adjusted), finds `80 < 100`, and **breaks** — the request is not unlocked.
7. `nextLockedNonce[stETH]` is not advanced. The user's withdrawal and all subsequent withdrawals are permanently stalled until the vault is manually topped up.
8. `assetsCommitted[stETH]` remains 100, so `getAvailableAssetAmount` = `totalAssets - 100`, blocking new withdrawal initiations as well. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L798-815)
```text
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

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

**File:** contracts/LRTWithdrawalManager.sol (L846-851)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```

**File:** contracts/external/eigenlayer/interfaces/IDelegationManager.sol (L313-316)
```text
     *
     * All withdrawn shares/strategies are placed in a queue and can be withdrawn after a delay. Withdrawals
     * are still subject to slashing during the delay period so the amount withdrawn on completion may actually be less
     * than what was queued if slashing has occurred in that period.
```

**File:** contracts/NodeDelegator.sol (L386-396)
```text
        if (receiveAsTokens) {
            for (uint256 i; i < assetCount; i++) {
                if (address(assets[i]) == LRTConstants.ETH_TOKEN) {
                    emit EthTransferred(address(_getUnstakingVault()), address(this).balance - balancesBefore[i]);
                    _getUnstakingVault().receiveFromNodeDelegator{ value: address(this).balance - balancesBefore[i] }();
                } else {
                    assets[i].safeTransfer(
                        address(_getUnstakingVault()), assets[i].balanceOf(address(this)) - balancesBefore[i]
                    );
                }
            }
```
