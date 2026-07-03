### Title
Unbounded Nested Loop Gas Consumption in User-Facing Deposit Path - (`contracts/LRTDepositPool.sol`, `contracts/NodeDelegator.sol`)

### Summary
Every call to `depositETH` and `depositAsset` in `LRTDepositPool` triggers a deeply nested loop: it iterates over all NDCs in `nodeDelegatorQueue`, and for each NDC calls `NodeDelegator.getAssetUnstaking()`, which itself contains nested loops over all EigenLayer queued withdrawals and their strategies. The outer loop bound (`maxNodeDelegatorLimit`) has no enforced upper cap, and the inner loop bound is the live EigenLayer queued withdrawal count per NDC. As the protocol scales, this chain of loops can exhaust the block gas limit, permanently preventing any user from depositing.

### Finding Description

`depositETH` and `depositAsset` both call `_beforeDeposit`, which calls `_checkIfDepositAmountExceedesCurrentLimit`, which calls `getTotalAssetDeposits`, which calls `getAssetDistributionData` (or `getETHDistributionData` for ETH):

```solidity
// LRTDepositPool.sol L447-456
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    unchecked { ++i; }
}
``` [1](#0-0) 

For each NDC, `getAssetUnstaking` is called, which fetches **all** queued withdrawals from EigenLayer and iterates over them with a nested loop:

```solidity
// NodeDelegator.sol L409-426
(IDelegationManager.Withdrawal[] memory queuedWithdrawals, ...) =
    _getDelegationManager().getQueuedWithdrawals(address(this));

for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    ...
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
``` [2](#0-1) 

The outer loop bound is `nodeDelegatorQueue.length`, which is capped by `maxNodeDelegatorLimit`. Critically, `updateMaxNodeDelegatorLimit` enforces only a **lower** bound (cannot shrink below current queue length) but **no upper bound**:

```solidity
// LRTDepositPool.sol L291-296
function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
    if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
        revert InvalidMaximumNodeDelegatorLimit();
    }
    maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
``` [3](#0-2) 

The same nested loop pattern also appears in `getETHDistributionData`, which is called for ETH deposits: [4](#0-3) 

Additionally, `LRTOracle._getTotalEthInProtocol()` iterates over all supported assets and calls `getTotalAssetDeposits` for each, compounding the loop depth further — and `updateRSETHPrice()` is publicly callable: [5](#0-4) 

### Impact Explanation

The total gas cost of a single `depositETH` or `depositAsset` call scales as:

`O(NDC_count × queued_withdrawals_per_NDC × strategies_per_withdrawal)`

Each inner iteration involves external calls to EigenLayer (cold storage reads). With `maxNodeDelegatorLimit` having no hard cap, and `maxUncompletedWithdrawalCount` settable up to 80 per the comment in `LRTUnstakingVault`, the worst-case iteration count is `NDCs × 80 × strategies`. As the protocol legitimately scales (more NDCs added for operational reasons), deposits will eventually revert with out-of-gas, **temporarily freezing all user deposits**.

The same path also makes `updateRSETHPrice()` (public, no access control) susceptible to out-of-gas, which would freeze oracle price updates and halt fee minting.

### Likelihood Explanation

The protocol is designed to scale: `maxNodeDelegatorLimit` starts at 10 but is explicitly upgradeable. As TVL grows, more NDCs are expected to be added. Each NDC can accumulate up to `maxUncompletedWithdrawalCount` (max 80) queued EigenLayer withdrawals during normal unstaking operations. This is a foreseeable operational state, not a hypothetical edge case.

### Recommendation

1. Cache the total asset distribution off-chain or in a storage variable updated lazily, rather than recomputing it on every deposit.
2. Enforce a hard upper bound on `maxNodeDelegatorLimit` (e.g., ≤ 10 or ≤ 15).
3. Decouple `getAssetUnstaking` from the deposit gas path — store a running `assetUnstaking` tally updated only when withdrawals are initiated or completed, rather than re-querying EigenLayer on every deposit.
4. Consider splitting `getAssetDistributionData` into a view-only function not called in state-changing paths.

### Proof of Concept

1. Admin adds 15 NDCs (sets `maxNodeDelegatorLimit = 15`, calls `addNodeDelegatorContractToQueue`).
2. Operator initiates unstaking from each NDC across 4 strategies, accumulating 60 queued withdrawals per NDC (within the 80 limit).
3. User calls `depositETH(0, "")`.
4. Execution path: `depositETH` → `_beforeDeposit` → `getTotalAssetDeposits` → `getAssetDistributionData` → 15 NDC iterations, each calling `getAssetUnstaking` → 60 withdrawal iterations × 4 strategy iterations = 3,600 inner loop steps, each with cold EigenLayer storage reads.
5. Transaction reverts with out-of-gas; no user can deposit ETH until the number of NDCs or queued withdrawals is reduced. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L290-296)
```text
    function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
        if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
            revert InvalidMaximumNodeDelegatorLimit();
        }

        maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
        emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
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

**File:** contracts/LRTDepositPool.sol (L484-493)
```text
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

**File:** contracts/NodeDelegator.sol (L406-427)
```text
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
