### Title
Unbounded Gas Consumption in `getAssetUnstaking()` Called on Every Deposit and Withdrawal Initiation - (File: contracts/NodeDelegator.sol)

### Summary

`NodeDelegator.getAssetUnstaking()` fetches all queued EigenLayer withdrawals and iterates over them with a nested loop. This function is invoked on every user deposit and every withdrawal initiation through the TVL accounting chain, causing gas costs to scale with the number of pending unstaking operations across all NodeDelegators.

### Finding Description

Every call to `depositETH()` or `depositAsset()` in `LRTDepositPool` triggers `_beforeDeposit()`, which calls `_checkIfDepositAmountExceedesCurrentLimit()`, which calls `getTotalAssetDeposits()`, which calls `getAssetDistributionData()` (or `getETHDistributionData()`). Both distribution functions loop over every NodeDelegator in `nodeDelegatorQueue` and call `getAssetUnstaking()` on each one. [1](#0-0) [2](#0-1) 

`getAssetUnstaking()` in `NodeDelegator` fetches the full list of queued withdrawals from EigenLayer's `DelegationManager` and iterates over them with a nested loop — outer over all withdrawals, inner over all strategies per withdrawal: [3](#0-2) 

The same chain is triggered by `initiateWithdrawal()` in `LRTWithdrawalManager`, which calls `getAvailableAssetAmount()` → `getTotalAssetDeposits()`: [4](#0-3) [5](#0-4) 

The public `updateRSETHPrice()` in `LRTOracle` also triggers the same chain through `_getTotalEthInProtocol()`, which loops over all supported assets and calls `getTotalAssetDeposits()` for each: [6](#0-5) [7](#0-6) 

The protocol itself acknowledges this gas concern in the comment inside `setMaxUncompletedWithdrawalCount()`: [8](#0-7) 

The comment reads: *"120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price"* — a direct admission that the loop cost is a real operational constraint. The cap is set at 80, but this is a shared counter across all NDCs. With up to `maxNodeDelegatorLimit` NDCs (initialized to 10), each deposit call executes: `NDC_count × queued_withdrawals_per_NDC × strategies_per_withdrawal` iterations, all as part of a single user transaction. [9](#0-8) 

### Impact Explanation

As the number of pending EigenLayer unstaking operations grows toward the cap, the gas cost of every deposit and every withdrawal initiation increases proportionally. At or near the cap, these user-facing transactions can exceed the block gas limit and revert, temporarily freezing deposits and withdrawal initiations for all users. This constitutes **temporary freezing of funds** (users cannot deposit or queue withdrawals).

### Likelihood Explanation

During periods of high withdrawal demand — a normal operational scenario — operators will queue many `initiateUnstaking()` calls. The protocol is designed to operate with up to 80 uncompleted withdrawals. At this level, every deposit and withdrawal initiation by any user executes the nested loop at maximum depth. This is a realistic steady-state condition, not a theoretical edge case.

### Recommendation

Cache the `assetUnstaking` value in storage and update it only when withdrawals are queued or completed (in `initiateUnstaking()` and `completeUnstaking()`), rather than recomputing it by iterating over all EigenLayer queued withdrawals on every read. This mirrors the fix described in the reference report: store the necessary values incrementally so that per-user calls do not require full iteration.

### Proof of Concept

1. Operators call `initiateUnstaking()` on multiple NDCs until `uncompletedWithdrawalCount` approaches 80.
2. Any unprivileged user calls `depositETH(1 ether, "")`.
3. The call chain executes: `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits` → `getETHDistributionData` → for each of N NDCs: `getAssetUnstaking(ETH_TOKEN)` → `getQueuedWithdrawals()` + nested loop over all queued withdrawals and strategies.
4. With 10 NDCs each holding 8 queued withdrawals of 3 strategies each, the inner loop executes 240 iterations plus 10 external `getQueuedWithdrawals()` calls, all within a single deposit transaction.
5. The transaction gas cost grows to the point where it approaches or exceeds the block gas limit, causing the deposit to revert. [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/LRTDepositPool.sol (L49-49)
```text
        maxNodeDelegatorLimit = 10;
```

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

**File:** contracts/LRTWithdrawalManager.sol (L168-170)
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
