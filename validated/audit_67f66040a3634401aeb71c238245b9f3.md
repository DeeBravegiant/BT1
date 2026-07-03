Audit Report

## Title
Unrestricted Public `updateRSETHPrice()` Enables Block Stuffing via O(assets × NDCs) Gas Amplification — (`contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is callable by any unprivileged address with no cooldown, no rate limit, and no access control beyond `whenNotPaused`. Its internal gas cost scales as O(supported\_assets × nodeDelegatorQueue.length) due to nested iteration with multiple external calls per NDC. An attacker can batch-call this function repeatedly within a single block to consume the full block gas limit, crowding out legitimate user transactions.

## Finding Description

`updateRSETHPrice()` is declared `public` with only the `whenNotPaused` modifier:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which iterates over every supported asset and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` → `getAssetDistributionData(asset)`. For each NDC in `nodeDelegatorQueue`, three external calls are made per LST asset (`IERC20.balanceOf`, `INodeDelegator.getAssetBalance`, `INodeDelegator.getAssetUnstaking`), and for the ETH path (`getETHDistributionData`) three more calls per NDC (`balance`, `getEffectivePodShares`, `getAssetUnstaking`).

Within the same block, underlying asset balances and NDC state do not change, so `totalETHInProtocol` is identical on every repeated call. After the first call updates `rsETHPrice`, subsequent calls compute `previousTVL ≈ totalETHInProtocol`, yielding `protocolFeeInETH = 0`. With no fee, `newRsETHPrice ≤ highestRsethPrice`, so the `PriceAboveDailyThreshold` revert is never triggered. `_checkAndUpdateDailyFeeMintLimit(0)` is a no-op (adds 0 to `currentPeriodMintedFeeAmount`). The function completes successfully on every repeated call.

There is no `lastUpdateBlock`, `lastUpdateTimestamp`, per-block call counter, or minimum ETH cost imposed on the caller beyond transaction gas fees.

## Impact Explanation

**Low — Block stuffing.**

With `A` supported assets and `N` NDCs (default `maxNodeDelegatorLimit = 10`), each `updateRSETHPrice()` call performs approximately `A × (1 oracle call + 3N external calls)` external calls plus storage reads. At `A = 5`, `N = 10`, that is ~155 external calls per invocation. At ~2,100 gas per cold external call plus overhead, a single call costs on the order of 400K–800K gas. An attacker can therefore fill a 30M gas block with ~40–75 such calls, preventing any legitimate deposit, withdrawal, or unstake transaction from being included for the duration of the attack.

## Likelihood Explanation

**Low-to-Medium.** The function is unconditionally public. No special token balance, role, or protocol state is required. The only cost to the attacker is the gas itself (block gas limit × base fee + priority fee). At low base-fee periods the sustained cost per block is reduced, making targeted griefing (e.g., during a time-sensitive withdrawal window) economically feasible for a motivated adversary.

## Recommendation

1. **Add a per-block or per-epoch cooldown**: record `lastUpdateBlock` or `lastUpdateTimestamp` and revert if called again within the same block (or within a configurable minimum interval).
2. **Restrict the caller**: gate `updateRSETHPrice()` behind `onlyLRTManager` or a dedicated keeper role, keeping `updateRSETHPriceAsManager()` as the sole public-facing privileged path.
3. **Cap iteration depth**: enforce a hard upper bound on `supportedAssetList.length` and `nodeDelegatorQueue.length` to limit worst-case gas per call regardless of access control.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

interface ILRTOracle {
    function updateRSETHPrice() external;
}

contract BlockStuffingPoC {
    ILRTOracle public oracle;

    constructor(address _oracle) {
        oracle = ILRTOracle(_oracle);
    }

    /// @notice Fill a block by calling updateRSETHPrice() N times.
    function stuffBlock(uint256 n) external {
        for (uint256 i; i < n; ++i) {
            oracle.updateRSETHPrice();
        }
    }
}
```

**Steps:**
1. Deploy a fork with `maxNodeDelegatorLimit` NDCs (e.g., 10) and the maximum number of supported assets.
2. Deploy `BlockStuffingPoC` pointing at the `LRTOracle` proxy.
3. Call `stuffBlock(N)` and measure `gasleft()` before and after to confirm per-call gas.
4. Compute `floor(30_000_000 / gasPerCall)` to find how many calls fill one block.
5. Assert that the ETH cost to fill one block (`30_000_000 × baseFee`) is below the economic threshold for a sustained griefing campaign.