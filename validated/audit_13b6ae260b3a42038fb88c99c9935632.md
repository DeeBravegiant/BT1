### Title
Unbounded Gas Consumption in `updateRSETHPrice` Due to Nested Loops Over Queued EigenLayer Withdrawals — (`contracts/NodeDelegator.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function whose gas cost grows as `O(supportedAssets × NDCs × queuedWithdrawals × strategies)`. The innermost dimension — the number of pending EigenLayer withdrawals per NDC — is unbounded by any protocol parameter and accumulates through normal protocol operation. Under realistic deployment conditions the function can exceed the Ethereum block gas limit, permanently freezing rsETH price updates and all flows that depend on them.

---

### Finding Description

The call chain is:

```
updateRSETHPrice()                          [public, no role check]
  └─ _updateRsETHPrice()
       └─ _getTotalEthInProtocol()
            └─ for each supportedAsset:          // loop A
                 getTotalAssetDeposits(asset)
                   └─ getAssetDistributionData(asset)
                        └─ for each NDC:         // loop B
                             getAssetUnstaking(asset)
                               └─ getQueuedWithdrawals(NDC)  // external call
                                    └─ for each withdrawal:  // loop C
                                         for each strategy:  // loop D
```

**Loop A** — `supportedAssets.length`: governed by `LRTConfig.addNewSupportedAsset` (requires `TIME_LOCK_ROLE`). Realistically 5–8 assets. [1](#0-0) 

**Loop B** — `nodeDelegatorQueue.length`: bounded by `maxNodeDelegatorLimit` (default 10, admin-adjustable upward without ceiling). [2](#0-1) 

**Loop C × D** — `queuedWithdrawals.length × withdrawal.strategies.length`: returned by EigenLayer's `DelegationManager.getQueuedWithdrawals(NDC)`. This count is **not bounded by any protocol parameter**. It grows each time an operator calls `queueWithdrawal` on EigenLayer and shrinks only when `completeQueuedWithdrawal` is called. A backlog of 50–100 pending withdrawals per NDC is operationally realistic during high-volume unstaking periods. [3](#0-2) 

Critically, `getQueuedWithdrawals` is called **once per (asset, NDC) pair**, not once per NDC. With 5 assets and 10 NDCs, it is called **50 times**, each returning the full withdrawal list for that NDC. The total inner-loop iterations are `5 × 10 × W × S` where `W` is the per-NDC withdrawal queue depth and `S` is strategies per withdrawal.

`updateRSETHPrice` carries no access control beyond `whenNotPaused`, so any caller can trigger it at any time. [4](#0-3) 

The same `getTotalAssetDeposits` path is also invoked inside `depositAsset` (via `getRsETHAmountToMint` → `_checkIfDepositAmountExceedesCurrentLimit`), so deposit flows are similarly affected once the queue grows large enough. [5](#0-4) 

---

### Impact Explanation

If `updateRSETHPrice` reverts with out-of-gas:

- The rsETH/ETH price is frozen at its last value.
- Protocol fee accrual stops.
- The price-decrease circuit-breaker (`_pause` on excessive drop) cannot fire.
- Any downstream flow that calls `getTotalAssetDeposits` for all assets (e.g., `getAssetCurrentLimit`) also becomes uncallable.

This matches **Medium — Unbounded gas consumption** and **Medium — Temporary freezing of funds** (deposits/withdrawals that depend on a fresh price).

---

### Likelihood Explanation

- No attacker action is required; the condition arises from normal protocol operation (operators queuing EigenLayer withdrawals faster than they are completed).
- `maxNodeDelegatorLimit` defaults to 10 and can be raised by admin; the protocol is designed to scale NDC count.
- EigenLayer imposes no cap on the number of pending queued withdrawals per staker.
- The condition is self-reinforcing: once gas exceeds the block limit, `completeQueuedWithdrawal` calls (which would shrink the queue) still work individually, but the oracle cannot update until the queue is manually drained — which requires off-chain operator intervention.

---

### Recommendation

1. **Cache `getQueuedWithdrawals` per NDC** across all assets in a single call rather than calling it once per `(asset, NDC)` pair. This reduces the external call count from `assets × NDCs` to `NDCs`.

2. **Introduce a per-NDC withdrawal queue depth cap** enforced at `queueWithdrawal` time, or store a running `assetUnstaking` accumulator updated on queue/complete events rather than recomputing it on every read.

3. **Decouple price update from full TVL recomputation**: store per-asset TVL snapshots updated lazily, and have `updateRSETHPrice` aggregate the snapshots rather than recomputing from scratch.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";

// Fork test — run against a mainnet/Holesky fork with real EigenLayer
contract GasExhaustionPoC is Test {
    ILRTOracle oracle = ILRTOracle(ORACLE_ADDR);

    function test_updateRSETHPrice_gasExhaustion() external {
        // Precondition: 5 supported assets, 10 NDCs,
        // each NDC has 80 pending queued withdrawals in EigenLayer
        // (achieved by calling DelegationManager.queueWithdrawal 80 times per NDC
        //  without completing them — normal operator behaviour during high redemption load)

        uint256 gasBefore = gasleft();
        oracle.updateRSETHPrice();
        uint256 gasUsed = gasBefore - gasleft();

        // Assert stays below 15M (Ethereum block gas limit ~30M, safe headroom)
        assertLt(gasUsed, 15_000_000, "updateRSETHPrice exceeds safe gas budget");
    }
}
```

With 5 assets × 10 NDCs × 80 withdrawals × 1 strategy each:
- 50 external calls to `getQueuedWithdrawals` (each returning 80-element arrays)
- 4 000 inner-loop iterations in `getAssetUnstaking`
- Each iteration includes an external `strategy.sharesToUnderlyingView` call and a `lrtConfig.beaconChainETHStrategy()` call

Estimated gas at ~5 000–8 000 gas per inner iteration plus external call overhead easily exceeds 15 M gas, and approaches or exceeds the 30 M block limit at higher queue depths or NDC counts.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L333-348)
```text
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
```

**File:** contracts/LRTDepositPool.sol (L446-456)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }
```

**File:** contracts/NodeDelegator.sol (L405-427)
```text
    function getAssetUnstaking(address asset) external view returns (uint256 amount) {
        (IDelegationManager.Withdrawal[] memory queuedWithdrawals, uint256[][] memory withdrawalShares) =
            _getDelegationManager().getQueuedWithdrawals(address(this));

        for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
            IDelegationManager.Withdrawal memory withdrawal = queuedWithdrawals[withdrawalIndex];

            for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
                IStrategy strategy = withdrawal.strategies[strategyIndex];

                address strategyAsset = address(strategy) == address(lrtConfig.beaconChainETHStrategy())
                    ? LRTConstants.ETH_TOKEN
                    : address(strategy.underlyingToken());

                if (strategyAsset != asset) continue;

                uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
                amount += strategyAsset == LRTConstants.ETH_TOKEN
                    ? sharesToUnstake
                    : strategy.sharesToUnderlyingView(sharesToUnstake);
            }
        }
    }
```
