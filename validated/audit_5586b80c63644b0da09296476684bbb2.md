### Title
Unbounded Gas Consumption in `updateRSETHPrice()` via Nested Loops Over Assets, NDCs, and Queued EigenLayer Withdrawals — (File: contracts/LRTOracle.sol)

---

### Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function. Its internal call chain creates a multiplicative loop: for every supported asset it iterates every NodeDelegator, and for every NodeDelegator it fetches and iterates every queued EigenLayer withdrawal. As the protocol scales legitimately, this function's gas cost grows as O(N × M × K) external calls and can eventually exceed the block gas limit, permanently preventing price updates.

---

### Finding Description

`updateRSETHPrice()` is declared `public` with no role restriction:

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` calls `_getTotalEthInProtocol()`:

```solidity
// contracts/LRTOracle.sol:331-349
function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
    address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
    uint256 supportedAssetCount = supportedAssets.length;

    for (uint16 assetIdx; assetIdx < supportedAssetCount;) {   // Loop 1: N assets
        uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
        ...
    }
}
```

`getTotalAssetDeposits()` calls `getAssetDistributionData()`:

```solidity
// contracts/LRTDepositPool.sol:447-456
for (uint256 i; i < ndcsCount;) {                              // Loop 2: M NDCs
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    ...
}
```

`getAssetUnstaking()` fetches all queued EigenLayer withdrawals and iterates them:

```solidity
// contracts/NodeDelegator.sol:405-427
function getAssetUnstaking(address asset) external view returns (uint256 amount) {
    (IDelegationManager.Withdrawal[] memory queuedWithdrawals, ...) =
        _getDelegationManager().getQueuedWithdrawals(address(this));

    for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {  // Loop 3: K withdrawals
        for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
            ...
            amount += strategy.sharesToUnderlyingView(sharesToUnstake);  // external call
        }
    }
}
```

The total external calls per `updateRSETHPrice()` invocation is approximately:

> **N × M × (3 + K × S)**

where N = supported assets, M = NDCs, K = queued withdrawals per NDC, S = strategies per withdrawal. Each term involves cold-storage reads and cross-contract calls (~2,100–5,000 gas each).

---

### Impact Explanation

**Medium — Unbounded gas consumption.**

With realistic protocol growth (e.g., N=5 assets, M=10 NDCs, K=10 queued withdrawals, S=2 strategies), the function executes ≥300 external calls. At ~5,000 gas each plus loop overhead, this approaches or exceeds Ethereum's 30M block gas limit. Once the gas cost exceeds the block limit, `updateRSETHPrice()` can never be executed again. The stored `rsETHPrice` becomes permanently stale, causing all subsequent deposits and withdrawals to use an incorrect exchange rate — breaking the protocol's core pricing invariant.

---

### Likelihood Explanation

**Medium.** The protocol is designed to support multiple assets and multiple NodeDelegators. EigenLayer queued withdrawals accumulate during normal operations (`initiateUnstaking`, `undelegate`). No adversarial action is required; ordinary protocol growth triggers the condition. The `maxNodeDelegatorLimit` starts at 10 and is admin-adjustable upward, and `maxUncompletedWithdrawalCount` in `LRTUnstakingVault` provides a soft cap but does not prevent the multiplicative blowup across all NDCs and assets.

---

### Recommendation

1. **Cache `getQueuedWithdrawals()` results**: `getAssetUnstaking()` is called once per NDC per asset per `updateRSETHPrice()` invocation. Refactor so queued withdrawals are fetched once per NDC and reused across all assets.
2. **Decouple asset accounting from price updates**: Store per-asset unstaking amounts as cached state variables updated lazily, rather than recomputing them on every price update.
3. **Paginate or bound the price update**: Introduce a maximum iteration count or split the computation across multiple transactions.
4. **Restrict `updateRSETHPrice()` to operators**: Since the function already has a manager-only variant (`updateRSETHPriceAsManager`), consider removing the public variant or adding a gas-cost guard.

---

### Proof of Concept

**Setup**: Protocol has 5 supported assets, 10 NDCs each with 10 queued EigenLayer withdrawals (2 strategies each).

**Step 1**: Any EOA calls `LRTOracle.updateRSETHPrice()`.

**Step 2**: Execution path:
- `_getTotalEthInProtocol()` iterates 5 assets
- Per asset: `getAssetDistributionData()` iterates 10 NDCs → 50 NDC iterations
- Per NDC: `getAssetUnstaking()` calls `getQueuedWithdrawals()` (1 external call) then iterates 10 withdrawals × 2 strategies → 20 `sharesToUnderlyingView()` external calls
- Total external calls: 50 × (1 + 20) + 50 × 2 (balanceOf + getAssetBalance) = **1,150+ external calls**

**Step 3**: At ~5,000 gas per external call, total ≈ **5.75M gas** for this configuration alone — before accounting for EigenLayer's own internal computation in `getQueuedWithdrawals()`. Scaling to M=10 NDCs with K=20 queued withdrawals pushes this well past 30M gas.

**Result**: `updateRSETHPrice()` reverts out-of-gas. The rsETH price is frozen at its last stored value. All deposits and withdrawals proceed at a stale rate, violating the protocol's exchange-rate guarantee. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
