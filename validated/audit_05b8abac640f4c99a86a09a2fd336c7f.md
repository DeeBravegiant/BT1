Audit Report

## Title
Push-Pattern ETH Transfer in `_processWithdrawalCompletion` Permanently Freezes Funds for Non-Payable Contract Withdrawers - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager._processWithdrawalCompletion` pushes ETH to the withdrawer via `payable(to).call{value: amount}("")`. If the withdrawer is a smart contract that cannot receive ETH, every call to `completeWithdrawal` or `completeWithdrawalForUser` reverts permanently. Because rsETH is already burned during the prior `unlockQueue` step, the user loses both their rsETH and their ETH with no admin recovery path.

## Finding Description
The withdrawal lifecycle is a three-step process:

**Step 1 — `initiateWithdrawal` (L150–178):** The user's rsETH is transferred into `LRTWithdrawalManager` and a withdrawal request is queued. No check is performed to verify that `msg.sender` can receive ETH.

**Step 2 — `unlockQueue` (L305–307):** The operator burns the queued rsETH via `IRSETH.burnFrom` and pulls the corresponding ETH from `LRTUnstakingVault` into `LRTWithdrawalManager`. After this step, the user's rsETH is gone and the ETH sits in the contract.

**Step 3 — `_processWithdrawalCompletion` (L699–738):** The function deletes the request record (L712), decrements `unlockedWithdrawalsCount[asset]` (L717), and then calls `_transferAsset` (L734). For ETH, `_transferAsset` (L876–883) executes:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```

If `to` is a contract without a `receive()` function (or one that reverts), `sent` is `false` and the entire transaction reverts — unwinding the `delete` and the counter decrement. The state is restored, but the underlying condition (the contract cannot receive ETH) is permanent. Every subsequent call to `completeWithdrawal` or `completeWithdrawalForUser` hits the same revert.

The developer comment at L191 acknowledges ETH-specific concerns for `completeWithdrawalForUser` (`/// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH`) but provides no alternative recovery path.

`sweepRemainingAssets` (L395–413) is gated by `hasUnlockedWithdrawals(asset)` (L403), which checks `unlockedWithdrawalsCount[asset] > 0`. Since the frozen request's counter can never decrement, this sweep is permanently blocked for all users of that asset.

## Impact Explanation
**Critical — Permanent freezing of funds.** Once `unlockQueue` burns rsETH and moves ETH into `LRTWithdrawalManager`, the only delivery path is `_processWithdrawalCompletion`. If that path is permanently blocked for a given address, the ETH is stranded in the contract with no admin function to redirect or recover it. The user loses both their rsETH (burned, irreversible) and their ETH (undeliverable). Additionally, `sweepRemainingAssets` is permanently blocked for the affected asset, compounding the impact across all users of that asset.

## Likelihood Explanation
**Medium.** Smart contract wallets (Gnosis Safe, multisigs, DAO treasuries) are primary holders of large rsETH positions and are the most likely to initiate ETH withdrawals. Many such contracts do not implement a `receive()` function. The protocol performs no on-chain check at `initiateWithdrawal` time to verify ETH receivability. No attacker is required — the scenario is triggered by ordinary protocol usage from a contract address.

## Recommendation
Replace the push pattern for ETH with a pull pattern in `_processWithdrawalCompletion`. Instead of calling `payable(to).call{value: amount}("")` directly, record the owed amount in a mapping (e.g., `mapping(address => uint256) public pendingETHWithdrawals`) and emit an event. Provide a separate `claimETH()` function that the user calls to pull their ETH. A failed pull only affects the caller's own transaction and cannot permanently freeze funds or block `sweepRemainingAssets`.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// A contract with no receive() — rejects all ETH
contract NoReceive {
    function exploit(
        address withdrawalManager,
        address rsETH,
        uint256 rsETHAmount
    ) external {
        // Step 1: initiateWithdrawal succeeds — rsETH transferred in, request queued
        IERC20(rsETH).approve(withdrawalManager, rsETHAmount);
        ILRTWithdrawalManager(withdrawalManager)
            .initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");

        // Step 2: operator calls unlockQueue externally
        //   -> rsETH burned via burnFrom
        //   -> ETH moved from LRTUnstakingVault into LRTWithdrawalManager

        // Step 3: completeWithdrawal always reverts with EthTransferFailed
        //   because NoReceive has no receive() function
        ILRTWithdrawalManager(withdrawalManager)
            .completeWithdrawal(ETH_TOKEN, ""); // ALWAYS REVERTS
        // ETH permanently locked; unlockedWithdrawalsCount[ETH_TOKEN] never decrements
    }
}
```

**Reproduction steps:**
1. Deploy `NoReceive` and fund it with rsETH.
2. Call `exploit` — `initiateWithdrawal` succeeds.
3. Operator calls `unlockQueue` — rsETH is burned, ETH moved to `LRTWithdrawalManager`.
4. Any call to `completeWithdrawal` or `completeWithdrawalForUser` for `NoReceive` reverts at `_transferAsset` with `EthTransferFailed`.
5. ETH is permanently locked; `unlockedWithdrawalsCount[ETH_TOKEN]` never reaches zero; `sweepRemainingAssets` is permanently blocked for ETH.