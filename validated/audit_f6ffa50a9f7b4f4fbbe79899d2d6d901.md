### Title
Nested Loop in `updateRSETHPrice()` Can OOG, Permanently Blocking Price Updates - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is publicly callable and internally executes a three-level nested loop across supported assets, node delegators, and queued EigenLayer withdrawals. As the protocol scales, the cumulative gas cost can exceed the block gas limit, making the price update permanently uncallable.

### Finding Description

`updateRSETHPrice()` calls `_updateRsETHPrice()` → `_getTotalEthInProtocol()`, which loops over every supported asset: [1](#0-0) 

For each asset, it calls `ILRTDepositPool.getTotalAssetDeposits(asset)` → `getAssetDistributionData(asset)`, which loops over every NDC in `nodeDelegatorQueue`: [2](#0-1) 

For each NDC, it calls `INodeDelegator.getAssetUnstaking(asset)`, which itself contains a **nested loop** over all queued EigenLayer withdrawals and all strategies within each withdrawal: [3](#0-2) 

The resulting call depth is:

```
updateRSETHPrice()
  └─ _getTotalEthInProtocol()                  [loop: N supported assets]
       └─ getTotalAssetDeposits(asset)
            └─ getAssetDistributionData(asset)  [loop: M NDCs]
                 └─ getAssetUnstaking(asset)    [loop: K withdrawals × L strategies]
```

Each iteration involves multiple external calls and storage reads. With, for example, 5 assets × 10 NDCs × 50 queued withdrawals × 3 strategies = 7,500 innermost iterations, each costing thousands of gas (external calls, `sharesToUnderlyingView`, storage reads), the total gas easily exceeds 30M.

The entry point has no access control: [4](#0-3) 

### Impact Explanation

If `updateRSETHPrice()` becomes uncallable due to OOG:

1. The rsETH price is permanently frozen at its last stored value.
2. The auto-pause mechanism that protects against price drops (`isPriceDecreaseOffLimit`) never triggers, disabling a critical safety guard.
3. Protocol fee minting is blocked, denying the treasury its earned yield.

The impact is **Medium — Unbounded gas consumption** making a critical protocol function permanently uncallable as the protocol scales. [5](#0-4) 

### Likelihood Explanation

**High.** The protocol is designed to scale: `maxNodeDelegatorLimit` is configurable upward, supported assets can be added via `addNewSupportedAsset`, and each NDC can accumulate many queued EigenLayer withdrawals over time. No attacker action is required — normal protocol growth causes the OOG. `updateRSETHPrice()` is callable by anyone, so there is no privileged gating to prevent the call from being attempted. [6](#0-5) 

### Recommendation

1. Cache the `getAssetUnstaking` result off-chain and push it on-chain via a privileged oracle update, rather than computing it live in `_getTotalEthInProtocol()`.
2. Alternatively, separate the price update into two transactions: one to snapshot per-NDC balances, and one to finalize the price from the snapshot.
3. Impose a hard cap on `maxUncompletedWithdrawalCount` per NDC and `maxNodeDelegatorLimit` that keeps the worst-case gas within safe bounds, and document the bound.

### Proof of Concept

Call chain that triggers OOG:

```
// Anyone calls:
LRTOracle.updateRSETHPrice()
  → _updateRsETHPrice()
  → _getTotalEthInProtocol()
      for each asset in supportedAssetList (N assets):
          LRTDepositPool.getTotalAssetDeposits(asset)
              → getAssetDistributionData(asset)
                  for each NDC in nodeDelegatorQueue (M NDCs):
                      NodeDelegator.getAssetUnstaking(asset)
                          → DelegationManager.getQueuedWithdrawals(ndc)
                          for each withdrawal (K):
                              for each strategy (L):
                                  strategy.sharesToUnderlyingView(shares)  // external call
```

With N=5, M=10, K=50, L=3: 7,500 external calls + storage reads. At ~5,000 gas each = 37.5M gas, exceeding the 30M block gas limit. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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
