### Title
Unbounded Gas Consumption in Publicly Callable `updateRSETHPrice()` Can Permanently Break the Oracle - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a `public` function with no access control and no rate limiting. It triggers a deeply nested chain of external calls whose total count grows as O(assets × NodeDelegators × queued EigenLayer withdrawals). Any unprivileged caller can invoke it at any time. As the protocol scales, the gas cost of a single call can exceed the block gas limit, permanently rendering the oracle uncallable and freezing the rsETH price.

### Finding Description

`updateRSETHPrice()` carries only a `whenNotPaused` modifier and is callable by any address:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which iterates over every supported asset and, for each asset, calls `ILRTDepositPool.getTotalAssetDeposits(asset)`:

```solidity
// contracts/LRTOracle.sol L336-348
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    unchecked { ++assetIdx; }
}
```

`getTotalAssetDeposits` calls `getAssetDistributionData`, which loops over every NodeDelegator and calls both `getAssetBalance` and `getAssetUnstaking` on each:

```solidity
// contracts/LRTDepositPool.sol L447-456
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    unchecked { ++i; }
}
```

`getAssetUnstaking` on each NodeDelegator calls EigenLayer's `getQueuedWithdrawals` and then iterates over every queued withdrawal and every strategy within it:

```solidity
// contracts/NodeDelegator.sol L406-427
(IDelegationManager.Withdrawal[] memory queuedWithdrawals, uint256[][] memory withdrawalShares) =
    _getDelegationManager().getQueuedWithdrawals(address(this));

for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
```

The total external call count is **O(assets × NDCs × queued_withdrawals_per_NDC)**. There is no cap on any of these dimensions at the oracle level.

### Impact Explanation

When the cumulative gas cost of `updateRSETHPrice()` exceeds the block gas limit:

- The function becomes permanently uncallable by anyone, including the manager via `updateRSETHPriceAsManager()` (which calls the same `_updateRsETHPrice()` path).
- `rsETHPrice` is frozen at its last stored value.
- Protocol fee minting stops.
- The price-drop circuit-breaker (which pauses `LRTDepositPool` and `LRTWithdrawalManager`) can no longer fire.
- Deposits and withdrawals continue using a stale exchange rate, causing share/asset mis-accounting for all users.

**Impact: Medium — Unbounded gas consumption leading to permanent freezing of unclaimed yield and stale oracle pricing.**

### Likelihood Explanation

The protocol already supports multiple LST assets (stETH, ETHx, etc.) and multiple NodeDelegator contracts. EigenLayer queued withdrawals accumulate during normal operation (each `initiateUnstaking` call adds one). As the protocol grows, the gas cost grows proportionally. An attacker does not need to do anything to trigger this — normal protocol growth is sufficient. The attacker's role is simply to call `updateRSETHPrice()` at the moment the gas cost is highest (e.g., during a period of many pending EigenLayer withdrawals) to confirm the function is broken, or to spam calls to accelerate discovery of the breakage.

### Recommendation

1. Add a caller restriction to `updateRSETHPrice()` (e.g., restrict to a keeper role or the manager), matching the pattern already used by `updateRSETHPriceAsManager()`.
2. Alternatively, introduce a cooldown/rate-limit so the function can only be called once per N blocks by unprivileged callers.
3. Refactor `_getTotalEthInProtocol()` to accept paginated inputs or cache intermediate values to bound per-call gas.

### Proof of Concept

Call chain for a single invocation of `updateRSETHPrice()` with A supported assets, N NodeDelegators, and W queued EigenLayer withdrawals per NDC:

```
updateRSETHPrice()
└─ _updateRsETHPrice()
   └─ _getTotalEthInProtocol()                          // A iterations
      └─ getTotalAssetDeposits(asset)                   // per asset
         └─ getAssetDistributionData(asset)             // N iterations
            ├─ IERC20.balanceOf(ndc[i])                 // N calls
            ├─ INodeDelegator.getAssetBalance(asset)    // N calls → EigenLayer strategyManager
            └─ INodeDelegator.getAssetUnstaking(asset)  // N calls
               └─ delegationManager.getQueuedWithdrawals()  // W iterations per NDC
```

With A=5 assets, N=10 NDCs, W=50 queued withdrawals each, a single call makes **~2,500+ external calls**. At ~2,100 gas per SLOAD and cross-contract call overhead, this approaches or exceeds the 30M block gas limit under realistic mainnet conditions. Any unprivileged address can call `updateRSETHPrice()` to confirm or trigger this condition.