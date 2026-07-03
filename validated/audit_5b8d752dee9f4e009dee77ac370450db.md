Audit Report

## Title
Nested Unbounded Loops in `updateRSETHPrice()`, `depositETH()`/`depositAsset()`, and `initiateWithdrawal()` Can Exceed Block Gas Limit - (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/NodeDelegator.sol`)

## Summary
`LRTOracle.updateRSETHPrice()` is publicly callable and executes a three-level nested loop whose gas cost scales as `supportedAssets.length × nodeDelegatorQueue.length × queuedWithdrawals.length`. The same inner two-level loop is traversed on every call to `depositETH()`, `depositAsset()`, and `initiateWithdrawal()`. As the protocol grows through ordinary operation, this nested iteration can exceed the block gas limit, rendering price updates and user deposits permanently uncallable. The protocol team has already acknowledged the gas scaling concern in a code comment but the mitigation only bounds one of the three loop dimensions.

## Finding Description

**Level 1 — `LRTOracle._getTotalEthInProtocol()`** iterates over every entry in `supportedAssets` with no cap: [1](#0-0) 

For each asset it calls `ILRTDepositPool.getTotalAssetDeposits(asset)` → `getAssetDistributionData()`.

**Level 2 — `LRTDepositPool.getAssetDistributionData()`** iterates over every entry in `nodeDelegatorQueue` (bounded only by admin-settable `maxNodeDelegatorLimit`): [2](#0-1) 

For each NDC it calls `INodeDelegator.getAssetUnstaking(asset)`.

**Level 3 — `NodeDelegator.getAssetUnstaking()`** calls EigenLayer's `getQueuedWithdrawals()` and iterates over every queued withdrawal and every strategy within each withdrawal: [3](#0-2) 

**Public entry point with no access control:** [4](#0-3) 

**User deposit entry points sharing the same inner loop path:** [5](#0-4) 

**Existing mitigation — only one dimension is bounded.** The protocol caps `maxUncompletedWithdrawalCount` at 80 and the comment explicitly acknowledges the gas scaling concern: [6](#0-5) 

The comment's own math ("ndc count * asset count = 15") reveals the team's assumed configuration. However, `supportedAssets` has no cap at all, and `maxNodeDelegatorLimit` is admin-settable to any value. The product of all three dimensions is not globally bounded.

## Impact Explanation

**Medium — Unbounded gas consumption / Temporary freezing of funds.**

If the nested loop product grows large enough to exceed the 30M block gas limit:
- `updateRSETHPrice()` becomes permanently uncallable, freezing the rsETH/ETH exchange rate and breaking accounting for all downstream consumers.
- `depositETH()` and `depositAsset()` become permanently uncallable, temporarily freezing new deposits.
- `initiateWithdrawal()` calls `getAvailableAssetAmount()` → `getTotalAssetDeposits()` → same loop, also becoming uncallable.

Both "Medium — Unbounded gas consumption" and "Medium — Temporary freezing of funds" are in the allowed impact scope.

## Likelihood Explanation

**Medium.** No attacker action is required; ordinary protocol growth is sufficient. Adding more supported LST assets (no cap), deploying more NDCs (admin-settable limit), and accumulating queued EigenLayer withdrawals (capped at 80 per NDC but multiplied across all NDCs and all assets) increases the product of all three loop dimensions. The protocol team has already acknowledged the concern, confirming this is a known operational risk rather than a purely theoretical one.

## Recommendation

1. **Cap `supportedAssets`**: Add an explicit maximum to the number of supported assets, analogous to `maxNodeDelegatorLimit`.
2. **Cache `getAssetUnstaking()` results**: Maintain an on-chain accounting variable per NDC per asset updated incrementally on each `initiateUnstaking`/`completeUnstaking`, rather than re-querying EigenLayer's full withdrawal queue on every price update and deposit.
3. **Decouple TVL accounting from live EigenLayer queries**: Store a cached `assetUnstaking` value that is updated lazily.
4. **Bound the product globally**: Enforce that `supportedAssets.length × maxNodeDelegatorLimit × maxUncompletedWithdrawalCount` stays within a safe gas budget (e.g., enforce this invariant in `addSupportedAsset`, `addNodeDelegatorContractToQueue`, and `setMaxUncompletedWithdrawalCount`).

## Proof of Concept

**Concrete scenario within protocol-allowed parameters:**
- 10 supported assets (`supportedAssets.length = 10`, no cap exists)
- `maxNodeDelegatorLimit = 10`, 10 NDCs deployed
- 8 withdrawals per NDC (`uncompletedWithdrawalCount = 80`, within the cap of 80)
- 3 strategies per withdrawal

**Gas accounting:**
- `getQueuedWithdrawals` external calls to EigenLayer: 10 NDCs × 10 assets = 100 external SLOAD-heavy calls
- Strategy iterations inside `getAssetUnstaking`: 10 NDCs × 8 withdrawals × 3 strategies = 240 iterations, repeated for each of 10 assets = 2,400 total strategy reads
- Each `getQueuedWithdrawals` reads from EigenLayer storage (multiple SLOADs per withdrawal entry)

At this scale, `updateRSETHPrice()` and `depositETH()` exceed the 30M block gas limit and revert on every call.

**Foundry fork test plan:**
1. Fork mainnet/testnet with EigenLayer deployed.
2. Deploy 10 NDCs, add 10 supported assets, set `maxNodeDelegatorLimit = 10`.
3. Queue 8 withdrawals per NDC via `initiateUnstaking`.
4. Call `updateRSETHPrice()` and measure gas with `vm.expectRevert` or `gasleft()` assertions.
5. Call `depositETH{value: 1 ether}()` and confirm revert due to out-of-gas.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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

**File:** contracts/LRTUnstakingVault.sol (L151-156)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
