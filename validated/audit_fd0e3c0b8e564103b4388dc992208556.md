Audit Report

## Title
Unbounded withdrawal queue flooding via uncapped `initiateWithdrawal` requests causes temporary freezing of legitimate user funds - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.initiateWithdrawal` imposes no per-user cap on pending withdrawal requests, and `minRsEthAmountToWithdraw[asset]` defaults to `0` for every asset until an admin explicitly sets it. An attacker can flood the global FIFO queue with dust requests, forcing the operator to exhaust the entire backlog before legitimate users' later-enqueued requests can be unlocked, temporarily freezing their already-transferred rsETH.

## Finding Description
`minRsEthAmountToWithdraw` is declared as a plain `mapping(address asset => uint256)` with no initialization in `initialize()`, so it is `0` for every asset by default:

```solidity
// L35
mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

The guard in `initiateWithdrawal` is:

```solidity
// L162-164
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

When `minRsEthAmountToWithdraw[asset] == 0`, the second condition `rsETHUnstaked < 0` is vacuously false for `uint256`, so any amount ≥ 1 wei is accepted. The `ExceedAmountToWithdraw` check at L170 does not prevent flooding because each 1-wei request commits only ~1 wei of `assetsCommitted`, which is negligible relative to total available assets.

Each accepted call pushes a new nonce into the global sequence:

```solidity
// L756-757
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

There is no per-user cap check anywhere before this push. The operator's `unlockQueue` → `_unlockWithdrawalRequests` must then walk every entry sequentially:

```solidity
// L790-815
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    unchecked { nextLockedNonce_++; }
}
nextLockedNonce[asset] = nextLockedNonce_;
```

`nextLockedNonce` is a single global cursor per asset. A request at nonce `N+1` (Alice's) cannot be unlocked until `nextLockedNonce` has advanced past all nonces `0..N`. Alice's rsETH is already transferred into the contract at `initiateWithdrawal` time (L166) and is inaccessible until the operator exhausts every preceding attacker entry.

## Impact Explanation
Alice's rsETH is transferred to the contract at request time and cannot be recovered until the operator processes all preceding attacker entries. With a large flood (e.g., tens of thousands of 1-wei requests), the operator must issue proportionally many `unlockQueue` batches — each bounded by block gas limits (~5,000–10,000 gas per loop iteration means ~3,000–6,000 entries per transaction at 30M gas). The delay is attacker-controlled and proportional to flood size. This constitutes **Medium — Temporary freezing of funds**, which is an explicitly listed valid impact.

## Likelihood Explanation
The attack requires no privileged access, no oracle manipulation, and no governance capture. Any rsETH holder can call `initiateWithdrawal` with 1-wei amounts repeatedly. The attacker recovers their own rsETH (minus gas) by completing their own dust withdrawals after the delay. The only cost is gas per transaction; on L2 deployments or during low-fee periods on mainnet, this is economically feasible. The attack is repeatable and can be re-executed after each operator flush.

## Recommendation
1. **Short term:** Enforce a non-zero `minRsEthAmountToWithdraw` for every supported asset at initialization time (e.g., `0.01 ether` worth of rsETH). Add a per-user cap on simultaneously pending withdrawal requests: `require(userAssociatedNonces[asset][msg.sender].length() < MAX_PENDING_PER_USER)` before the `pushBack` call in `_addUserWithdrawalRequest`.
2. **Long term:** Consider a per-asset global queue depth limit or a fee-per-request mechanism that makes flooding economically prohibitive.

## Proof of Concept
1. Confirm `minRsEthAmountToWithdraw[stETH] == 0` (never set after deployment).
2. Eve holds 10,000 wei of rsETH.
3. Eve calls `LRTWithdrawalManager.initiateWithdrawal(stETH, 1, "")` 10,000 times, occupying global nonces `0..9999`. Each call passes the L162 guard and commits ~1 wei of `assetsCommitted`.
4. Alice calls `initiateWithdrawal(stETH, 1e18, "")`, receiving nonce `10000`. Her rsETH is transferred to the contract at L166.
5. After `withdrawalDelayBlocks` pass, operator calls `unlockQueue(stETH, 10001, ...)`. The loop at L790 must iterate through all 10,000 Eve entries before reaching nonce `10000`. At ~6,000 entries per 30M-gas transaction, this requires at least 2 full operator transactions just to reach Alice's entry.
6. Alice's funds remain frozen for the entire duration. Eve can extend the freeze arbitrarily by increasing the flood size before Alice's request.