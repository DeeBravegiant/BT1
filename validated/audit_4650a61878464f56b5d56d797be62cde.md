Audit Report

## Title
ETH Permanently Frozen When Withdrawal Recipient Cannot Receive ETH - (File: `contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTWithdrawalManager` splits the withdrawal lifecycle across two committed transactions: `unlockQueue` burns rsETH and pulls ETH into the contract (irreversible), while `completeWithdrawal` delivers ETH to the user. If the user is a contract without a `receive()` function, the ETH delivery always reverts, but the rsETH burn from the prior transaction is permanent. No admin function exists to cancel, redirect, or recover the stuck ETH, and the only sweep path is permanently blocked.

## Finding Description
**Phase 1 — `unlockQueue` (operator-triggered, committed transaction):**
At `contracts/LRTWithdrawalManager.sol` L305–307, rsETH is burned via `burnFrom` and ETH is redeemed from `LRTUnstakingVault` into the contract. This transaction commits and cannot be rolled back.

**Phase 2 — `_processWithdrawalCompletion` (user-triggered):**
At L699–738, the function pops the nonce queue (L705), deletes the request (L712), decrements `unlockedWithdrawalsCount` (L717), then calls `_transferAsset(asset, user, request.expectedAssetAmount)` (L734).

`_transferAsset` at L876–883 executes:
```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```
If `to` is a contract without `receive()`, `sent` is `false` and `EthTransferFailed` is thrown. Because there is no `try/catch`, the revert propagates and rolls back all state changes within this transaction — the nonce pop, the request deletion, and the `unlockedWithdrawalsCount` decrement are all undone.

The result is a permanently stuck state:
- `userAssociatedNonces[asset][user]` still holds the nonce → every future `completeWithdrawal` call re-enters the same path and reverts identically.
- `unlockedWithdrawalsCount[asset]` remains > 0 → `hasUnlockedWithdrawals` at L629–631 permanently returns `true`.
- `sweepRemainingAssets` at L395–414 checks `if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist()` (L403), permanently blocking the only ETH egress path.
- No `cancelWithdrawal`, `redirectWithdrawal`, or equivalent admin function exists in the contract. `emergencyWithdrawFromAave` (L551–563) only covers Aave-deposited funds, not the contract's direct ETH balance.

The comment on `completeWithdrawalForUser` at L191 — *"Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH"* — acknowledges ETH transfer sensitivity but does not address the stuck-withdrawal scenario.

## Impact Explanation
**Critical — Permanent freezing of funds.** The user's rsETH is irreversibly burned in Phase 1. The corresponding ETH is locked inside `LRTWithdrawalManager` with no recovery path. This matches the allowed impact class "Permanent freezing of funds" exactly.

## Likelihood Explanation
Any smart contract that holds rsETH and calls `initiateWithdrawal(ETH_TOKEN, …)` without a `receive()` fallback triggers this condition. This includes multisig wallets (e.g., Gnosis Safe modules that do not forward ETH), DeFi vaults, yield aggregators, and protocol-owned liquidity managers — a realistic and growing class of on-chain integrators for a liquid restaking token. No special privilege is required: the depositor initiates the withdrawal, and the operator's routine `unlockQueue` call completes the trap. The condition is deterministic and repeatable.

## Recommendation
**Short term:** Add a `withdrawalRecipient` mapping allowing users to designate a separate payout address at `initiateWithdrawal` time, or add an admin-only `cancelWithdrawal(address asset, address user)` function that deletes the stuck request, decrements `unlockedWithdrawalsCount`, and returns the ETH to a configurable address.

**Long term:** At `initiateWithdrawal` time for ETH withdrawals, perform a pre-flight receivability check (analogous to ERC721's `_checkOnERC721Received`): attempt a zero-value call to `msg.sender` and revert with a descriptive error if it fails. This prevents the two-phase commitment from ever entering an unrecoverable state.

## Proof of Concept
Deploy the following contract, approve rsETH, and execute the sequence:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface ILRTWithdrawalManager {
    function initiateWithdrawal(address asset, uint256 rsETHUnstaked, string calldata referralId) external;
    function completeWithdrawal(address asset, string calldata referralId) external;
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
}

contract NoReceiveWallet {
    address constant ETH_TOKEN = 0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE;

    function step1_initiateWithdrawal(
        address withdrawalManager,
        address rsETH,
        uint256 rsETHAmount
    ) external {
        IERC20(rsETH).approve(withdrawalManager, rsETHAmount);
        ILRTWithdrawalManager(withdrawalManager).initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
        // rsETH is now held by LRTWithdrawalManager
    }

    function step3_completeWithdrawal(address withdrawalManager) external {
        // Operator has already called unlockQueue — rsETH burned, ETH in LRTWithdrawalManager
        // This ALWAYS reverts with EthTransferFailed: NoReceiveWallet has no receive()
        ILRTWithdrawalManager(withdrawalManager).completeWithdrawal(ETH_TOKEN, "");
    }
    // No receive() — ETH delivery always fails
    // rsETH permanently burned; ETH permanently locked; sweepRemainingAssets permanently blocked
}
```

**Sequence:**
1. `NoReceiveWallet.step1_initiateWithdrawal(...)` — rsETH transferred to `LRTWithdrawalManager` (L166).
2. Operator calls `unlockQueue(ETH_TOKEN, ...)` — rsETH burned (L305), ETH redeemed into contract (L307). Committed.
3. `NoReceiveWallet.step3_completeWithdrawal(...)` — reverts with `EthTransferFailed` (L879) on every call.
4. Verify: rsETH total supply is reduced; `LRTWithdrawalManager` ETH balance equals the stuck amount; `hasUnlockedWithdrawals(ETH_TOKEN)` returns `true`; `sweepRemainingAssets(ETH_TOKEN)` reverts with `PendingWithdrawalsExist`.