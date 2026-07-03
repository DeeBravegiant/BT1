Audit Report

## Title
Dust Withdrawal Requests Can Temporarily Freeze Legitimate Users' Funds via Queue Bloat - (`contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTWithdrawalManager.initiateWithdrawal()` accepts any non-zero rsETH amount because `minRsEthAmountToWithdraw` defaults to `0` and is never set in the initializer. An unprivileged attacker can flood the FIFO queue with near-zero requests before a legitimate user's request. Because `_unlockWithdrawalRequests` advances `nextLockedNonce` strictly in order with no skip mechanism, all preceding dust entries must be individually processed before the legitimate user's `completeWithdrawal` can succeed, temporarily freezing their funds beyond the normal 8-day delay.

## Finding Description

**Root cause — no effective dust floor:**

`minRsEthAmountToWithdraw` is a plain mapping whose Solidity default is `0`:

```solidity
mapping(address asset => uint256) public minRsEthAmountToWithdraw;
``` [1](#0-0) 

The `initialize()` function never sets a value for any asset: [2](#0-1) 

The guard in `initiateWithdrawal` therefore reduces to `rsETHUnstaked == 0` for every asset until an admin explicitly calls `setMinRsEthAmountToWithdraw`:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [3](#0-2) 

Any amount ≥ 1 wei is accepted.

**Queue insertion — strictly sequential nonces:**

Each accepted call appends to the global nonce counter:

```solidity
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
``` [4](#0-3) 

**Settlement loop — no skip mechanism:**

`_unlockWithdrawalRequests` advances `nextLockedNonce_` one step at a time with no ability to jump over entries: [5](#0-4) 

The operator-supplied `firstExcludedIndex` only sets an upper bound; it cannot skip dust entries that sit between `nextLockedNonce` and the legitimate request's nonce.

**Completion check — reverts until nonce is unlocked:**

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
``` [6](#0-5) 

The legitimate user's `completeWithdrawal` reverts until all preceding dust entries have been processed and `nextLockedNonce` advances past their nonce.

## Impact Explanation

Legitimate users' withdrawal requests are **temporarily frozen**: `completeWithdrawal` reverts with `WithdrawalLocked` for a period beyond the normal 8-day delay, proportional to the number of dust entries inserted before the victim's request. Each `unlockQueue` call is bounded by the block gas limit (~30M gas); at ~7,000–10,000 gas per iteration (cold storage), roughly 3,000–4,000 entries can be processed per call. An attacker inserting tens of thousands of dust entries forces the operator to issue many sequential `unlockQueue` calls before the victim's nonce is reached. This maps directly to **Medium — Temporary freezing of funds**.

## Likelihood Explanation

- `minRsEthAmountToWithdraw` is `0` by default for every asset; no initializer or deployment script sets it, so the vulnerability is live on deployment unless an admin proactively configures it.
- The attacker needs rsETH (a real but minimal economic cost — 1 wei per request) and gas per call. The per-entry cost is low relative to the delay imposed on the victim.
- The attack requires no privileged access and is reachable by any rsETH holder via the public `initiateWithdrawal` function.
- The 8-day `withdrawalDelayBlocks` means the attacker must pre-position dust requests before the victim, but this is trivially achievable by front-running or continuous queue flooding.

## Recommendation

1. **Enforce a non-zero default minimum in the initializer:**
   ```solidity
   minRsEthAmountToWithdraw[LRTConstants.ETH_TOKEN] = 0.001 ether;
   ```
2. **Require a non-zero floor before an asset is activated** for withdrawals, rather than relying on a post-deployment admin call.
3. Alternatively, add a **per-address rate limit** on `initiateWithdrawal` (e.g., one request per block per address) to prevent rapid queue flooding.

## Proof of Concept

```solidity
// Precondition: minRsEthAmountToWithdraw[ETH_TOKEN] == 0 (default, never set in initializer)

// Step 1: Attacker creates N dust withdrawal requests (e.g., N = 10_000)
for (uint i = 0; i < 10_000; i++) {
    withdrawalManager.initiateWithdrawal(ETH_TOKEN, 1 wei, "");
    // Each accepted; nonces 0..9999 consumed
}

// Step 2: Victim creates a legitimate withdrawal request
// Victim's request lands at nonce 10_000
withdrawalManager.initiateWithdrawal(ETH_TOKEN, 10 ether, "");

// Step 3: After 8-day delay, operator calls unlockQueue
// At ~7,000 gas/iteration and 30M gas limit, ~4,000 entries per call
// Operator needs ≥3 separate unlockQueue calls to reach nonce 10_000

// Step 4: Until all 10,000 dust entries are processed, victim's call reverts:
withdrawalManager.completeWithdrawal(ETH_TOKEN, ""); // reverts: WithdrawalLocked

// Foundry test sketch:
// 1. Deploy contracts on a local fork
// 2. Mint 10_000 wei rsETH to attacker; approve withdrawalManager
// 3. Loop: call initiateWithdrawal(ETH_TOKEN, 1 wei, "") 10_000 times
// 4. Mint 10 ether rsETH to victim; call initiateWithdrawal(ETH_TOKEN, 10 ether, "")
// 5. vm.roll(block.number + withdrawalDelayBlocks + 1)
// 6. Call unlockQueue with firstExcludedIndex = 10_001 — observe it only processes ~4_000 entries
// 7. Assert victim's completeWithdrawal reverts with WithdrawalLocked
// 8. Call unlockQueue two more times; assert victim's completeWithdrawal now succeeds
```

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

**File:** contracts/LRTWithdrawalManager.sol (L707-707)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L756-757)
```text
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
