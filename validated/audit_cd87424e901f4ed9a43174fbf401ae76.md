### Title
Unbounded Gas Consumption via Publicly Callable `updateRSETHPrice()` with Nested Loops Over Assets, NDCs, and EigenLayer Queued Withdrawals - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a public function with no access control that any unprivileged caller can invoke at will. Each invocation triggers a deeply nested computation: it iterates over all supported assets, and for each asset iterates over all NodeDelegator contracts (NDCs), and for each NDC calls `getAssetUnstaking()` which itself iterates over all queued EigenLayer withdrawals. There is no cooldown, rate limit, or per-call cost imposed on the caller. An attacker can spam this function to consume block gas, degrading liveness for all other protocol users.

### Finding Description

`LRTOracle.updateRSETHPrice()` is declared `public whenNotPaused` with no role check:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

`_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which loops over every supported asset and for each calls `ILRTDepositPool.getTotalAssetDeposits(asset)`:

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    uint256 assetER = getAssetPrice(asset);
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
``` [2](#0-1) 

`getTotalAssetDeposits` calls `getAssetDistributionData`, which loops over every NDC in `nodeDelegatorQueue` and calls `getAssetUnstaking` on each:

```solidity
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    ...
}
``` [3](#0-2) 

`NodeDelegator.getAssetUnstaking()` calls EigenLayer's `getQueuedWithdrawals()` and iterates over all queued withdrawals with a nested loop:

```solidity
for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
``` [4](#0-3) 

The same NDC loop pattern applies to ETH via `getETHDistributionData()`: [5](#0-4) 

The `_checkAndUpdateDailyFeeMintLimit` inside `_updateRsETHPrice` does not prevent repeated calls: when there is no TVL increase, `protocolFeeInETH == 0`, so `feeAmount == 0`, and the condition `currentPeriodMintedFeeAmount + 0 > maxFeeMintAmountPerDay` never triggers a revert (especially when `maxFeeMintAmountPerDay` is 0, the default unset value). There is therefore no on-chain rate limiting on how frequently `updateRSETHPrice()` can be called. [6](#0-5) 

### Impact Explanation

**Medium — Unbounded gas consumption.** The total gas per call scales as `O(assets × NDCs × queued_withdrawals_per_NDC)`. With `maxNodeDelegatorLimit = 10` NDCs and an unbounded number of EigenLayer queued withdrawals per NDC, a single call can consume a large fraction of the block gas limit. An attacker can submit many such transactions per block at no protocol cost (no fee, no minimum deposit, no stake required), filling blocks and delaying or reverting legitimate user deposits, withdrawals, and price-sensitive operations that also call `updateRSETHPrice()` internally. [7](#0-6) 

### Likelihood Explanation

**High.** The entry point is unconditionally public, requires no tokens or collateral, and has no cooldown. Any EOA or contract can call it at any time the oracle is not paused. The attack is cheap relative to the gas consumed by the victim transactions it displaces. [1](#0-0) 

### Recommendation

1. **Add a cooldown**: Record `lastUpdateTimestamp` and revert if called again within a minimum interval (e.g., 1 block or a configurable period).
2. **Restrict to a role**: Gate `updateRSETHPrice()` behind `onlyLRTManager` or a dedicated `PRICE_UPDATER_ROLE`, keeping the permissioned `updateRSETHPriceAsManager()` as the sole public-facing path.
3. **Cap loop depth**: Enforce a hard cap on `nodeDelegatorQueue.length` (already `maxNodeDelegatorLimit = 10`) and consider caching or snapshotting EigenLayer queued withdrawal counts rather than iterating them on every price update.

### Proof of Concept

```
Attacker (EOA, no tokens needed):
  loop:
    call LRTOracle.updateRSETHPrice()
      → _updateRsETHPrice()
        → _getTotalEthInProtocol()
          → for each of N supported assets:
              → LRTDepositPool.getTotalAssetDeposits(asset)
                → getAssetDistributionData(asset)
                  → for each of up to 10 NDCs:
                      → NodeDelegator.getAssetUnstaking(asset)
                        → DelegationManager.getQueuedWithdrawals(ndc)
                          → iterate all queued withdrawals (unbounded)
```

Each iteration is a separate external call. With 5 assets × 10 NDCs × K queued withdrawals, gas per call grows as 50K external calls. The attacker pays only their own gas; there is no protocol-level fee or stake burned. Repeated across many transactions per block, this constitutes a sustained unbounded gas consumption attack that degrades liveness for all other users. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
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

**File:** contracts/LRTDepositPool.sol (L49-49)
```text
        maxNodeDelegatorLimit = 10;
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
