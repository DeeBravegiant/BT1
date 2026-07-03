### Title
Nested Loops with External Calls in `updateRSETHPrice()` Can Cause Unbounded Gas Consumption — (File: contracts/LRTOracle.sol)

---

### Summary

The publicly callable `updateRSETHPrice()` function in `LRTOracle.sol` triggers a chain of nested loops, each containing external calls to EigenLayer contracts. As the protocol scales (more supported assets, more NodeDelegators, more queued EigenLayer withdrawals), the cumulative gas cost grows multiplicatively and can approach or exceed the block gas limit, permanently preventing price updates and blocking all protocol operations that depend on a fresh rsETH price.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is `public whenNotPaused` — callable by any address. It internally calls `_getTotalEthInProtocol()`:

```solidity
// LRTOracle.sol
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_getTotalEthInProtocol()` loops over every supported asset and, for each, calls `getTotalAssetDeposits()`:

```solidity
// LRTOracle.sol _getTotalEthInProtocol()
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    ...
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

`getTotalAssetDeposits()` → `getAssetDistributionData()` loops over every NodeDelegator and, for each, calls `getAssetUnstaking()`:

```solidity
// LRTDepositPool.sol getAssetDistributionData()
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    ...
}
```

`getAssetUnstaking()` in `NodeDelegator.sol` calls EigenLayer's `getQueuedWithdrawals()` and then runs a **nested loop** over every queued withdrawal and every strategy within it:

```solidity
// NodeDelegator.sol getAssetUnstaking()
(IDelegationManager.Withdrawal[] memory queuedWithdrawals, ...) =
    _getDelegationManager().getQueuedWithdrawals(address(this));

for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
        amount += strategy.sharesToUnderlyingView(sharesToUnstake); // external call
    }
}
```

The total iteration count is: **`supportedAssets × NDCs × queuedWithdrawals × strategiesPerWithdrawal`**, with each innermost iteration making external calls to EigenLayer strategy contracts. The same call chain is also triggered by every user deposit via `depositAsset()` / `depositETH()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()`.

---

### Impact Explanation

If the cumulative gas cost of `updateRSETHPrice()` or `depositAsset()`/`depositETH()` exceeds the block gas limit:

- **`updateRSETHPrice()` becomes uncallable**: the stored `rsETHPrice` grows stale, fee minting is blocked, and the protocol's TVL accounting breaks.
- **`depositAsset()` / `depositETH()` become uncallable**: users cannot deposit assets into the protocol — **temporary freezing of funds** (Medium severity per scope).
- **`initiateWithdrawal()` in `LRTWithdrawalManager`** also calls `getAvailableAssetAmount()` → `getTotalAssetDeposits()`, so withdrawal initiation is similarly blocked.

Impact: **Medium — Unbounded gas consumption / Temporary freezing of funds.**

---

### Likelihood Explanation

The protocol's `maxNodeDelegatorLimit` starts at 10 and can be raised by admin. `maxUncompletedWithdrawalCount` can be set up to 80. With 5 supported assets, 10 NDCs, and 80 queued withdrawals each containing multiple strategies, the iteration count reaches thousands of external calls per transaction. This is a realistic operational state for a growing restaking protocol, making the likelihood **Medium**.

---

### Recommendation

1. **Cache intermediate results**: Store per-NDC asset balances in a mapping updated lazily rather than recomputing on every call.
2. **Decouple price update from full TVL scan**: Use an incremental or off-chain-assisted price update mechanism rather than iterating all NDCs and EigenLayer queued withdrawals on-chain in a single transaction.
3. **Limit loop depth explicitly**: Enforce a hard cap on the product of `supportedAssets × NDCs` and document the maximum safe `maxUncompletedWithdrawalCount` relative to the block gas limit.
4. **Paginate or batch**: Allow `updateRSETHPrice()` to accept a subset of assets/NDCs per call, accumulating results across multiple transactions.

---

### Proof of Concept

Call chain reachable by any unprivileged address:

```
updateRSETHPrice()                          [LRTOracle.sol — public]
  └─ _getTotalEthInProtocol()              [LRTOracle.sol L336 — loops over N assets]
       └─ getTotalAssetDeposits(asset)     [LRTDepositPool.sol L385]
            └─ getAssetDistributionData()  [LRTDepositPool.sol L447 — loops over M NDCs]
                 └─ getAssetUnstaking()    [NodeDelegator.sol L409 — loops over K withdrawals × S strategies]
                      └─ strategy.sharesToUnderlyingView()  [external call per strategy]
```

Gas cost ≈ O(N × M × K × S × external_call_cost). With N=5, M=10, K=80, S=2, and ~5,000 gas per external SLOAD-heavy call, total gas easily exceeds 30M (Ethereum block gas limit). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
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
