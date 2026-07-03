### Title
Missing Minimum Withdrawal Amount Allows Dust Request Queue Flooding, Temporarily Freezing Legitimate Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.initiateWithdrawal` enforces no effective minimum rsETH amount when `minRsEthAmountToWithdraw[asset]` is at its default value of zero. An unprivileged rsETH holder can flood the global withdrawal queue with 1-wei dust requests at negligible cost, forcing the operator's `unlockQueue` to iterate through all dust entries before legitimate requests can be unlocked, temporarily freezing other users' withdrawals.

### Finding Description
`minRsEthAmountToWithdraw` is a per-asset mapping that is never initialized in `initialize()` and therefore defaults to `0` for every asset. [1](#0-0) 

The guard in `initiateWithdrawal` is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [2](#0-1) 

When `minRsEthAmountToWithdraw[asset] == 0` (the default), the condition collapses to `rsETHUnstaked == 0`, meaning any amount ≥ 1 wei passes. Each accepted call pushes a new nonce into the global sequential queue and increments `nextUnusedNonce[asset]`: [3](#0-2) 

The operator's `unlockQueue` processes requests strictly in nonce order via `_unlockWithdrawalRequests`. It cannot skip individual entries; `nextLockedNonce[asset]` advances one-by-one: [4](#0-3) 

If an attacker pre-fills nonces 0–N with dust requests, any legitimate request at nonce N+1 cannot be unlocked until all N dust entries are processed first. Processing N entries in a single `unlockQueue` call costs O(N) gas; with enough dust entries the call runs out of gas, and the operator must split work across many transactions — or the queue stalls entirely.

The admin setter exists but provides no protection unless proactively called before the first deposit: [5](#0-4) 

### Impact Explanation
Legitimate users who submitted withdrawal requests after the dust flood cannot call `completeWithdrawal` until their nonce falls below `nextLockedNonce[asset]`. Because `nextLockedNonce` advances only through `unlockQueue`, and `unlockQueue` must iterate through every dust entry in order, legitimate withdrawals are **temporarily frozen** until the operator exhausts the dust queue — which may require many expensive L1 transactions or be practically infeasible if the queue is large enough. This maps to **Medium — Temporary freezing of funds**.

### Likelihood Explanation
The attacker only needs rsETH (obtainable by depositing ETH into `LRTDepositPool`) and the ability to call `initiateWithdrawal` repeatedly. No privileged role is required. The cost per dust request is one L1 transaction plus 1 wei of rsETH. On a chain where gas is cheap (or if the attacker is willing to spend), flooding thousands of entries is realistic. The default-zero `minRsEthAmountToWithdraw` makes every fresh deployment vulnerable until an admin explicitly sets the minimum.

### Recommendation
1. Set a non-zero default for `minRsEthAmountToWithdraw` inside `initialize()` (e.g., `1e15` wei rsETH, ~0.001 rsETH) for every supported asset.
2. Alternatively, enforce a protocol-wide floor in `initiateWithdrawal` independent of the per-asset mapping, so the contract is safe even before the admin configures individual limits.
3. Consider adding a per-user pending-request cap to bound queue growth regardless of amount.

### Proof of Concept
1. Deploy or use a live `LRTWithdrawalManager` where `minRsEthAmountToWithdraw[stETH] == 0` (default).
2. Attacker acquires a small amount of rsETH (e.g., 10 000 wei).
3. Attacker calls `initiateWithdrawal(stETH, 1, "")` 10 000 times, each transferring 1 wei rsETH and pushing a new nonce.
4. A legitimate user calls `initiateWithdrawal(stETH, 1e18, "")` — their request lands at nonce 10 000.
5. Operator calls `unlockQueue(stETH, 10001, ...)`. The while-loop must iterate all 10 001 entries; at sufficient scale this exceeds the block gas limit.
6. The legitimate user's `completeWithdrawal` reverts with `WithdrawalLocked` because `nextLockedNonce[stETH]` never reaches their nonce.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L330-333)
```text
    function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
        minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
        emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L755-757)
```text
        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
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
