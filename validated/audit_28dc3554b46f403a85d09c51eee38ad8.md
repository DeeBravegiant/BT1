### Title
Unbounded Gas Consumption in `getAssetUnstaking()` Nested Loop Permanently Freezes Deposits and Withdrawals — (File: `contracts/NodeDelegator.sol`)

---

### Summary

`NodeDelegator.getAssetUnstaking()` contains a nested loop over all EigenLayer queued withdrawals and their strategies. This function is called for every NDC in the deposit and withdrawal critical paths. As queued withdrawals accumulate — including through EigenLayer operator-forced undelegations that bypass the protocol's soft cap — the gas cost of `depositETH()`, `depositAsset()`, and `initiateWithdrawal()` grows unboundedly, eventually exceeding the block gas limit and permanently freezing user funds.

---

### Finding Description

`NodeDelegator.getAssetUnstaking()` iterates over every queued EigenLayer withdrawal and every strategy within each withdrawal: [1](#0-0) 

This function is called inside `getETHDistributionData()` and `getAssetDistributionData()` for **every NDC** in `nodeDelegatorQueue`: [2](#0-1) [3](#0-2) 

Both of these feed into `getTotalAssetDeposits()`, which is on the critical path for:

1. **Deposits** — `depositETH()` / `depositAsset()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()`: [4](#0-3) 

2. **Withdrawals** — `initiateWithdrawal()` → `getAvailableAssetAmount()` → `getTotalAssetDeposits()`: [5](#0-4) 

3. **Price updates** — `updateRSETHPrice()` → `_getTotalEthInProtocol()` → `getTotalAssetDeposits()` per asset: [6](#0-5) 

The effective gas complexity is **O(NDCs × queued\_withdrawals\_per\_NDC × strategies\_per\_withdrawal)**.

The protocol attempts to mitigate this with `maxUncompletedWithdrawalCount` capped at 80: [7](#0-6) 

However, this cap is a **protocol-level accounting counter** that tracks withdrawals initiated through the protocol's own flow. EigenLayer operator-forced undelegations create queued withdrawals in EigenLayer directly, bypassing this counter. The protocol's own comment acknowledges this: [8](#0-7) 

Furthermore, `increaseUncompletedWithdrawalCount()` does not enforce the cap — it only increments: [9](#0-8) 

The actual EigenLayer queued withdrawal count (queried live by `getQueuedWithdrawals()`) can therefore exceed the protocol's soft cap through forced undelegations, making the mitigation incomplete.

---

### Impact Explanation

If the total number of queued EigenLayer withdrawals across all NDCs grows large enough that the nested loop in `getAssetUnstaking()` exhausts the block gas limit, then `depositETH()`, `depositAsset()`, and `initiateWithdrawal()` all revert. Users cannot deposit or withdraw, resulting in **permanent freezing of funds** (Critical) or at minimum **temporary freezing** (Medium) until withdrawals are completed and the count drops.

`updateRSETHPrice()` would also become uncallable, causing the rsETH price to go stale, which further breaks the protocol's accounting.

---

### Likelihood Explanation

The `maxNodeDelegatorLimit` is initialized to 10 NDCs. With 10 NDCs each holding multiple EigenLayer withdrawal requests (from normal unstaking operations plus forced undelegations), the nested loop in `getAssetUnstaking()` is called 10 times per deposit/withdrawal, each time iterating over all queued withdrawals × strategies. The protocol's own comment acknowledges that beyond 120 total uncompleted withdrawals, `updateRSETHPrice()` fails. Forced undelegations from EigenLayer operators — an external, non-admin-controlled event — can push the count past the threshold without any action from the protocol.

---

### Recommendation

1. **Cache queued withdrawal data off-chain** and pass it as a calldata parameter to avoid live EigenLayer enumeration in the deposit/withdrawal hot path.
2. **Decouple `getAssetUnstaking()` from the deposit/withdrawal path** — store a running tally of unstaking amounts updated at queue/complete time rather than recomputing it by iterating EigenLayer state on every user interaction.
3. **Enforce a hard cap** on the number of queued EigenLayer withdrawals per NDC that is checked at queue time (including forced undelegations via a reconciliation mechanism), not just a soft protocol counter.

---

### Proof of Concept

1. Protocol has 10 NDCs (`maxNodeDelegatorLimit = 10`).
2. EigenLayer operators force-undelegate all NDCs, creating 8 queued withdrawals per NDC (each with 3 strategies) = 80 queued withdrawals × 3 strategies = 240 loop iterations in `getAssetUnstaking()` alone, called 10 times = 2,400 external calls to EigenLayer strategy contracts.
3. A user calls `depositETH()`. The call chain reaches `getETHDistributionData()` → 10 × `getAssetUnstaking(ETH_TOKEN)` → 10 × (loop over 8 withdrawals × 3 strategies).
4. Each `getQueuedWithdrawals()` call and `strategy.sharesToUnderlyingView()` call costs significant gas. At scale, the transaction reverts with out-of-gas.
5. All deposits and withdrawals are permanently blocked until EigenLayer withdrawal delays expire and `completeUnstaking()` is called for each — a process that takes at minimum 7 days per EigenLayer's withdrawal delay, during which all user funds are frozen. [10](#0-9) [2](#0-1) [4](#0-3) [11](#0-10)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L482-493)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L168-171)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
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

**File:** contracts/LRTUnstakingVault.sol (L150-158)
```text
    function setMaxUncompletedWithdrawalCount(uint256 _maxUncompletedWithdrawalCount) external onlyLRTManager {
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;

        emit MaxUncompletedWithdrawalCountSet(_maxUncompletedWithdrawalCount);
```

**File:** contracts/LRTUnstakingVault.sol (L184-186)
```text
    function increaseUncompletedWithdrawalCount() external onlyLRTNodeDelegator {
        uncompletedWithdrawalCount++;
    }
```
