Audit Report

## Title
Uninitialized `minRsEthAmountToWithdraw` Allows Queue Flooding Leading to Unbounded Gas Consumption and Temporary Freezing of Funds - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`minRsEthAmountToWithdraw` is a `mapping(address => uint256)` that defaults to `0` for every asset and is never initialized in `initialize()`. The guard in `initiateWithdrawal` is vacuously false for any non-zero amount when the mapping is unset, allowing any rsETH holder to flood the FIFO withdrawal queue with 1-wei dust entries. Because `_unlockWithdrawalRequests` iterates the queue strictly in order, legitimate users' requests queued behind dust entries are temporarily frozen, and the operator's unlock transaction gas cost grows linearly with the number of queued entries.

## Finding Description
`initiateWithdrawal` checks:

```solidity
// LRTWithdrawalManager.sol:162
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

`minRsEthAmountToWithdraw` is declared at line 35 as `mapping(address asset => uint256) public minRsEthAmountToWithdraw` with no initialization in `initialize()` (lines 90–98). For any asset where the admin has not called `setMinRsEthAmountToWithdraw`, the value is `0`. The condition `rsETHUnstaked < 0` is vacuously false for `uint256`, so any amount ≥ 1 wei passes.

Each accepted request is appended to the global FIFO queue via `_addUserWithdrawalRequest` (lines 744–759), incrementing `nextUnusedNonce[asset]`.

The operator's unlock path calls `_unlockWithdrawalRequests` (lines 770–816), which iterates every entry from `nextLockedNonce[asset]` to `firstExcludedIndex` in a `while` loop (lines 790–814), performing multiple storage reads and writes per iteration (`withdrawalRequests`, `assetsCommitted`, `unlockedWithdrawalsCount`). The loop has no early-exit for dust entries — it processes every entry in order. Because the queue is strictly FIFO, legitimate requests queued after dust entries cannot be unlocked until all preceding dust entries are processed. If `firstExcludedIndex` is set to `nextUnusedNonce[asset]` after a large flood, the loop exhausts the block gas limit. Even with a bounded `firstExcludedIndex`, the operator must drain all dust entries across many transactions before reaching legitimate requests.

The `availableAssetAmount < payoutAmount` break condition at line 800 does not help: for 1-wei rsETH, `payoutAmount` rounds to 0 or 1 wei, which is always ≤ `availableAssetAmount`, so the loop never breaks early on dust entries.

## Impact Explanation
**Medium — Temporary freezing of funds / Unbounded gas consumption.**

Legitimate withdrawers whose requests are queued behind a flood of dust entries cannot complete their withdrawals (line 707: `if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked()`) until the operator processes every preceding entry. The operator's unlock transaction gas cost scales linearly with the number of queued entries; a sufficiently large flood causes the transaction to revert out-of-gas, stalling the entire withdrawal queue for the affected asset. Both "Medium. Unbounded gas consumption" and "Medium. Temporary freezing of funds" are concretely demonstrated.

## Likelihood Explanation
Any rsETH holder can call `initiateWithdrawal` without any special role. The attacker's only cost is gas per call and temporary lock-up of negligible rsETH (recovered after the withdrawal delay via `completeWithdrawal`). The attack is cheap, repeatable, and requires no coordination or privileged access. The precondition — `minRsEthAmountToWithdraw[asset] == 0` — holds by default for every asset until an admin explicitly calls `setMinRsEthAmountToWithdraw`, which is not enforced anywhere in the deployment or asset-support flow.

## Recommendation
1. Initialize `minRsEthAmountToWithdraw` to a sensible non-zero default (e.g., `1e15` wei) for each supported asset in `initialize()`, or require it to be set before an asset is usable for withdrawals.
2. Enforce the minimum strictly so that a zero mapping value is not treated as "no minimum":
   ```solidity
   if (rsETHUnstaked == 0 || minRsEthAmountToWithdraw[asset] == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
       revert InvalidAmountToWithdraw();
   }
   ```
3. Consider adding a per-user cap on the number of pending withdrawal requests to further limit queue flooding.

## Proof of Concept
1. Deploy `LRTWithdrawalManager` and support asset `ETH`. `minRsEthAmountToWithdraw[ETH]` is `0` (never set by admin).
2. Attacker acquires N wei of rsETH (e.g., N = 10,000).
3. Attacker calls `initiateWithdrawal(ETH, 1, "")` N times. Each call passes line 162 (`1 == 0` is false; `1 < 0` is false for uint256). Each call appends a new entry to the queue via `_addUserWithdrawalRequest`.
4. Legitimate user calls `initiateWithdrawal(ETH, 1 ether, "")`. Their request is at nonce N.
5. Operator calls `unlockQueue` with `firstExcludedIndex = nextUnusedNonce[ETH]` (N+1). The `_unlockWithdrawalRequests` while loop iterates all N+1 entries. At ~5,000+ gas per iteration (storage reads/writes to `withdrawalRequests`, `assetsCommitted`, `unlockedWithdrawalsCount`), N = 5,000 entries exhausts the 30M mainnet block gas limit.
6. Even with a bounded `firstExcludedIndex`, the operator must issue ceil(N / batch_size) transactions to drain the dust entries. The legitimate user's withdrawal (nonce N) is locked (`WithdrawalLocked` revert at line 707) until `nextLockedNonce[ETH]` advances past N, temporarily freezing their funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

**File:** contracts/LRTWithdrawalManager.sol (L90-98)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        withdrawalDelayBlocks = 8 days / 12 seconds;

        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L744-759)
```text
    function _addUserWithdrawalRequest(address asset, uint256 rsETHUnstaked, uint256 expectedAssetAmount) internal {
        uint256 nextUnusedNonce_ = nextUnusedNonce[asset];

        // Generate a unique identifier for the new withdrawal request.
        bytes32 requestId = getRequestId(asset, nextUnusedNonce_);

        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });

        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;

        emit AssetWithdrawalQueued(msg.sender, asset, rsETHUnstaked, nextUnusedNonce_);
```

**File:** contracts/LRTWithdrawalManager.sol (L790-815)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
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
