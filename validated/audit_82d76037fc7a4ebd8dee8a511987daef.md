### Title
Unset Minimum Withdrawal Amount Allows Queue Flooding to Delay Legitimate Withdrawals - (File: `contracts/LRTWithdrawalManager.sol`)

### Summary
`LRTWithdrawalManager.initiateWithdrawal` accepts any non-zero rsETH amount because `minRsEthAmountToWithdraw[asset]` defaults to `0`. An unprivileged attacker can submit thousands of 1-wei rsETH withdrawal requests, filling the sequential withdrawal queue ahead of legitimate users. Because `_unlockWithdrawalRequests` advances a global cursor (`nextLockedNonce`) that cannot skip entries, operators must process every spam entry before any later legitimate withdrawal can be unlocked, temporarily freezing those users' funds.

### Finding Description
`initiateWithdrawal` enforces:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [1](#0-0) 

`minRsEthAmountToWithdraw` is a `mapping(address => uint256)` whose default value is `0` for every asset until an admin explicitly calls `setMinRsEthAmountToWithdraw`. [2](#0-1) 

When the mapping value is `0`, the condition `rsETHUnstaked < 0` is always false for `uint256`, so the guard reduces to `rsETHUnstaked == 0` only. Any amount ≥ 1 wei of rsETH is accepted and appended to the queue.

The queue is consumed by `_unlockWithdrawalRequests`, which starts from the global cursor `nextLockedNonce[asset]` and increments it one-by-one with no ability to skip entries:

```solidity
uint256 nextLockedNonce_ = nextLockedNonce[asset];
while (nextLockedNonce_ < firstExcludedIndex) {
    bytes32 requestId = getRequestId(asset, nextLockedNonce_);
    WithdrawalRequest storage request = withdrawalRequests[requestId];
    ...
    unchecked { nextLockedNonce_++; }
}
nextLockedNonce[asset] = nextLockedNonce_;
``` [3](#0-2) 

Every spam entry occupies a slot in the global nonce sequence. Legitimate withdrawals queued after the spam cannot be unlocked until the cursor has advanced past all preceding entries.

### Impact Explanation
Legitimate users whose withdrawal requests are queued after the spam entries have their funds temporarily frozen: their rsETH has already been transferred to the contract at `initiateWithdrawal` time, but `completeWithdrawal` will revert (or return nothing) until the operator's `unlockQueue` calls have advanced `nextLockedNonce` past all spam entries. Each `unlockQueue` call that processes spam entries also consumes unbounded operator gas proportional to the number of spam entries in the batch. This maps to **Temporary freezing of funds (Medium)** and **Unbounded gas consumption (Medium)**.

### Likelihood Explanation
The attack requires only rsETH tokens (obtainable by depositing ETH/LSTs) and repeated calls to the public `initiateWithdrawal` function. No privileged access is needed. The cost per spam entry is 1 wei of rsETH plus transaction gas, making large-scale queue flooding economically feasible, especially on L2 deployments where gas is cheap. The default state of every new asset deployment has `minRsEthAmountToWithdraw == 0`, so the window is open unless an admin proactively sets a minimum.

### Recommendation
1. **Set a non-zero default minimum** in `initialize` (e.g., `minRsEthAmountToWithdraw[asset] = 1e15` for each supported asset), or enforce a protocol-wide floor inside `initiateWithdrawal` that does not rely on the admin having called `setMinRsEthAmountToWithdraw`.
2. **Expose a skip/cancel mechanism** that allows operators to remove or bypass zero-value or dust withdrawal entries from the queue without advancing the cursor through them one-by-one.

### Proof of Concept
1. Mallory holds 10,000 wei of rsETH (trivially obtained).
2. Mallory calls `initiateWithdrawal(ETH, 1, "")` 10,000 times. Each call passes the `rsETHUnstaked == 0` guard because `minRsEthAmountToWithdraw[ETH] == 0`. [1](#0-0) 
3. Alice calls `initiateWithdrawal(ETH, 10 ether, "")`. Her request is assigned nonce 10,000.
4. After the withdrawal delay, the operator calls `unlockQueue`. `_unlockWithdrawalRequests` starts at `nextLockedNonce[ETH] == 0` and must iterate through all 10,000 of Mallory's entries before reaching Alice's nonce. [4](#0-3) 
5. Until the operator has paid gas to process all 10,000 spam entries across multiple `unlockQueue` calls, Alice's rsETH remains locked in the contract and `completeWithdrawal` cannot finalize her request.

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

**File:** contracts/LRTWithdrawalManager.sol (L786-815)
```text
        uint256 nextLockedNonce_ = nextLockedNonce[asset];
        // Revert when trying to unlock a request that has already been unlocked
        if (nextLockedNonce_ >= firstExcludedIndex) revert NoPendingWithdrawals();

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
