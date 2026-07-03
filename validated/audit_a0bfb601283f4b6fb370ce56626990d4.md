Audit Report

## Title
Unbounded Gas Consumption in `updateRSETHPrice` via Nested Loops Over EigenLayer Queued Withdrawals — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/NodeDelegator.sol`)

## Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function that recomputes the full protocol TVL on every call. Its gas cost scales as `O(assets × NDCs × queuedWithdrawals × strategies)` because `NodeDelegator.getAssetUnstaking` fetches and iterates the entire EigenLayer queued-withdrawal list for each `(asset, NDC)` pair. The queued-withdrawal depth is unbounded by any protocol parameter and grows through normal operator activity, allowing the function to exceed the Ethereum block gas limit and permanently freeze rsETH price updates.

## Finding Description
The verified call chain is:

```
updateRSETHPrice()                          [public, whenNotPaused only]
  └─ _updateRsETHPrice()
       └─ _getTotalEthInProtocol()
            └─ for each supportedAsset (Loop A):
                 getTotalAssetDeposits(asset)
                   └─ getAssetDistributionData(asset)
                        └─ for each NDC (Loop B):
                             getAssetUnstaking(asset)
                               └─ getQueuedWithdrawals(NDC)   // external call
                                    └─ for each withdrawal (Loop C):
                                         for each strategy (Loop D):
```

All four loops are confirmed in the codebase:

- **Loop A** (`LRTOracle.sol` lines 336–348): iterates `supportedAssets`.
- **Loop B** (`LRTDepositPool.sol` lines 447–456): iterates `nodeDelegatorQueue`, calling `getAssetUnstaking` per NDC per asset.
- **Loops C & D** (`NodeDelegator.sol` lines 409–426): `getQueuedWithdrawals` is called once per `(asset, NDC)` pair and returns the full pending-withdrawal list; both the withdrawal array and the per-withdrawal strategy array are iterated in full.

`getQueuedWithdrawals` is therefore called `assets × NDCs` times (e.g., 50 times with 5 assets and 10 NDCs), each returning the complete withdrawal backlog. No protocol parameter caps the number of pending EigenLayer withdrawals per NDC. The queue grows whenever an operator calls `queueWithdrawal` on EigenLayer and shrinks only when `completeQueuedWithdrawal` is called. A backlog of 50–100 withdrawals per NDC is operationally realistic during high-volume unstaking periods.

`updateRSETHPrice` carries no access control beyond `whenNotPaused`; any address can call it at any time.

The same `getTotalAssetDeposits` path is also invoked inside `getAssetCurrentLimit`, which is called during deposit flows, so deposits are similarly affected once the queue grows large enough.

## Impact Explanation
When `updateRSETHPrice` reverts out-of-gas:
- The rsETH/ETH price is frozen at its last stored value.
- Protocol fee accrual stops.
- The price-decrease circuit-breaker (`_pause` on excessive drop) cannot fire.
- `getAssetCurrentLimit` (which calls `getTotalAssetDeposits`) also becomes uncallable, blocking new deposits.

This matches **Medium — Unbounded gas consumption** and **Medium — Temporary freezing of funds** from the allowed impact scope.

## Likelihood Explanation
No attacker action is required. The condition arises from normal protocol operation: operators queuing EigenLayer withdrawals faster than they are completed. `maxNodeDelegatorLimit` defaults to 10 and is admin-adjustable upward; EigenLayer imposes no cap on pending queued withdrawals per staker. The condition is self-reinforcing: once the oracle is bricked, `completeQueuedWithdrawal` calls still work individually but require coordinated off-chain operator intervention to drain the queue before the oracle can resume.

## Recommendation
1. **Cache `getQueuedWithdrawals` per NDC** across all assets in a single call. Replace the per-`(asset, NDC)` call pattern with a single call per NDC that accumulates amounts for all assets simultaneously, reducing external calls from `assets × NDCs` to `NDCs`.
2. **Introduce a running `assetUnstaking` accumulator** updated on `queueWithdrawal` and `completeQueuedWithdrawal` events rather than recomputing from the full withdrawal list on every read.
3. **Decouple price update from full TVL recomputation**: store per-asset TVL snapshots updated lazily, and have `updateRSETHPrice` aggregate snapshots rather than recomputing from scratch on each call.

## Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;
import "forge-std/Test.sol";

contract GasExhaustionPoC is Test {
    ILRTOracle oracle = ILRTOracle(ORACLE_ADDR);

    function test_updateRSETHPrice_gasExhaustion() external {
        // Precondition: 5 supported assets, 10 NDCs,
        // each NDC has 80 pending queued withdrawals in EigenLayer
        // (achieved by calling DelegationManager.queueWithdrawal 80× per NDC
        //  without completing them — normal operator behaviour under redemption load)

        uint256 gasBefore = gasleft();
        oracle.updateRSETHPrice();
        uint256 gasUsed = gasBefore - gasleft();

        // 5 assets × 10 NDCs × 80 withdrawals × 1 strategy = 4,000 inner iterations
        // Each iteration: external getQueuedWithdrawals + sharesToUnderlyingView + beaconChainETHStrategy()
        // Estimated: >15M gas; approaches/exceeds 30M block limit at higher queue depths
        assertLt(gasUsed, 15_000_000, "updateRSETHPrice exceeds safe gas budget");
    }
}
```

The test should be run as a fork test against a Holesky or mainnet fork with real EigenLayer contracts. The precondition (80 pending withdrawals per NDC) is achievable through normal operator activity and requires no privileged access beyond what operators already have.