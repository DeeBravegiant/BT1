### Title
Unbounded `nodeDelegatorQueue` Growth Causes Unbounded Gas Consumption in Critical Protocol Functions — (`contracts/LRTDepositPool.sol`, `contracts/LRTConfig.sol`, `contracts/LRTOracle.sol`, `contracts/LRTUnstakingVault.sol`)

---

### Summary

`updateMaxNodeDelegatorLimit()` imposes no upper bound on `maxNodeDelegatorLimit`, allowing an admin to raise it arbitrarily and then fill `nodeDelegatorQueue` via `addNodeDelegatorContractToQueue()`. Four critical functions iterate over the entire queue with multiple external calls per entry and no gas guard, making them susceptible to exceeding the block gas limit as the queue grows.

---

### Finding Description

`updateMaxNodeDelegatorLimit()` accepts any value ≥ `nodeDelegatorQueue.length` with no ceiling: [1](#0-0) 

`addNodeDelegatorContractToQueue()` is gated only by that limit: [2](#0-1) 

Four functions then iterate over the full queue with multiple external calls per NDC per iteration:

**1. `LRTConfig.pauseAll()`** — 2 external calls per NDC (`paused()` + `pause()`): [3](#0-2) 

**2. `LRTDepositPool.getAssetDistributionData()`** — 3 external calls per NDC per asset (`balanceOf`, `getAssetBalance`, `getAssetUnstaking`): [4](#0-3) 

**3. `LRTOracle.updateRSETHPrice()`** — calls `_getTotalEthInProtocol()` which calls `getTotalAssetDeposits()` per supported asset, each of which calls `getAssetDistributionData()`, multiplying the NDC loop by the number of supported assets: [5](#0-4) 

**4. `LRTUnstakingVault.setUncompletedWithdrawalCount()`** — 1 external call per NDC to `delegationManager.getQueuedWithdrawals()`: [6](#0-5) 

The protocol is explicitly aware of gas limits in adjacent code — `setMaxUncompletedWithdrawalCount` caps at 80 with the comment *"120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price / ndc count * asset count = 15"* — but applies no equivalent cap to `maxNodeDelegatorLimit`: [7](#0-6) 

---

### Impact Explanation

If `nodeDelegatorQueue.length` grows large enough (rough estimate: ~200–400 NDCs with 5 supported assets, given ~15,000–30,000 gas per NDC per asset for cold EigenLayer strategy reads), all four functions exceed the 30M block gas limit simultaneously:

- `pauseAll()` becomes uncallable → protocol cannot be emergency-paused.
- `updateRSETHPrice()` becomes uncallable → rsETH price is permanently stale.
- `getAssetDistributionData()` becomes uncallable → TVL accounting and deposit limits break.
- `setUncompletedWithdrawalCount()` becomes uncallable → withdrawal accounting breaks.

This constitutes **Medium — Unbounded gas consumption**, with a secondary path to **Medium — Temporary freezing of funds** (deposits rely on `getRsETHAmountToMint` → `getTotalAssetDeposits` → `getAssetDistributionData`).

---

### Likelihood Explanation

Likelihood is **low**. The initial `maxNodeDelegatorLimit` is 10, and the protocol's own gas-awareness comment implies ~5 NDCs in practice. Reaching a queue size that breaks the block gas limit requires the admin to deliberately raise the limit to hundreds and populate it — an operationally implausible but technically unconstrained action. No malicious intent is required; a misconfiguration or future scaling decision suffices.

---

### Recommendation

Add a hard ceiling to `updateMaxNodeDelegatorLimit()`, consistent with the gas-awareness already present in `setMaxUncompletedWithdrawalCount`. A safe upper bound (e.g., 50) should be derived from the worst-case gas cost of `_getTotalEthInProtocol()` across the maximum supported asset count, ensuring all four affected functions remain within the block gas limit.

```solidity
uint256 public constant MAX_NODE_DELEGATOR_HARD_LIMIT = 50;

function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
    if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) revert InvalidMaximumNodeDelegatorLimit();
    if (maxNodeDelegatorLimit_ > MAX_NODE_DELEGATOR_HARD_LIMIT) revert ExceedsHardLimit();
    maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
    emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Pseudocode for a Foundry fork test
function test_unboundedGasConsumption() public {
    // 1. Admin raises limit to 300
    vm.prank(admin);
    lrtDepositPool.updateMaxNodeDelegatorLimit(300);

    // 2. Deploy 300 mock NodeDelegator contracts and add them
    address[] memory ndcs = _deployMockNDCs(300);
    vm.prank(admin);
    lrtDepositPool.addNodeDelegatorContractToQueue(ndcs);

    // 3. Measure gas for each affected function
    uint256 gasBefore;

    gasBefore = gasleft();
    lrtConfig.pauseAll();
    uint256 pauseAllGas = gasBefore - gasleft();

    gasBefore = gasleft();
    lrtOracle.updateRSETHPrice();
    uint256 updatePriceGas = gasBefore - gasleft();

    gasBefore = gasleft();
    lrtDepositPool.getAssetDistributionData(stETH);
    uint256 distDataGas = gasBefore - gasleft();

    gasBefore = gasleft();
    lrtUnstakingVault.setUncompletedWithdrawalCount();
    uint256 withdrawalCountGas = gasBefore - gasleft();

    // 4. Assert at least one exceeds 30M (block gas limit)
    assertTrue(
        pauseAllGas > 30_000_000 ||
        updatePriceGas > 30_000_000 ||
        distDataGas > 30_000_000 ||
        withdrawalCountGas > 30_000_000,
        "At least one critical function exceeds block gas limit"
    );
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L290-297)
```text
    function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
        if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
            revert InvalidMaximumNodeDelegatorLimit();
        }

        maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
        emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
    }
```

**File:** contracts/LRTDepositPool.sol (L302-306)
```text
    function addNodeDelegatorContractToQueue(address[] calldata nodeDelegatorContracts) external onlyLRTAdmin {
        uint256 length = nodeDelegatorContracts.length;
        if (nodeDelegatorQueue.length + length > maxNodeDelegatorLimit) {
            revert MaximumNodeDelegatorLimitReached();
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

**File:** contracts/LRTConfig.sol (L273-282)
```text
        address[] memory nodeDelegatorQueue = ILRTDepositPool(address(lrtDepositPool)).getNodeDelegatorQueue();
        uint256 nodeDelegatorCount = nodeDelegatorQueue.length;

        for (uint256 i = 0; i < nodeDelegatorCount;) {
            IPausable nodeDelegator = IPausable(nodeDelegatorQueue[i]);
            if (!nodeDelegator.paused()) nodeDelegator.pause();
            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTOracle.sol (L336-348)
```text
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

**File:** contracts/LRTUnstakingVault.sol (L150-155)
```text
    function setMaxUncompletedWithdrawalCount(uint256 _maxUncompletedWithdrawalCount) external onlyLRTManager {
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
```

**File:** contracts/LRTUnstakingVault.sol (L168-175)
```text
        address[] memory nodeDelegatorQueue = lrtDepositPool.getNodeDelegatorQueue();
        uint256 totalQueued;
        for (uint256 i = 0; i < nodeDelegatorQueue.length; i++) {
            address nodeDelegator = nodeDelegatorQueue[i];
            (IDelegationManager.Withdrawal[] memory queuedWithdrawals,) =
                delegationManager.getQueuedWithdrawals(nodeDelegator);
            totalQueued += queuedWithdrawals.length;
        }
```
