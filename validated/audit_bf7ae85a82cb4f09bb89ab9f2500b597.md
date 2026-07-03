Audit Report

## Title
`dailyMintLimit` Uninitialized in `initialize()` Causes All Deposits to Revert Until Admin Intervenes - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

## Summary
All three pool contracts declare `dailyMintLimit` as a plain storage variable defaulting to `0`, and none of their `initialize()` functions assign it a value. The `limitDailyMint` modifier, applied to every `deposit()` entry point, unconditionally reverts with `DailyMintLimitExceeded` for any non-zero deposit when `dailyMintLimit == 0`, blocking all user deposits until an admin manually calls `setDailyMintLimit()` or the corresponding `reinitialize()`.

## Finding Description
In all three contracts, `dailyMintLimit` is declared without initialization:
- `RSETHPoolV3.sol` L51: `uint256 public dailyMintLimit;`
- `RSETHPoolV3ExternalBridge.sol` L67: `uint256 public dailyMintLimit;`
- `RSETHPoolV3WithNativeChainBridge.sol` L57: `uint256 public dailyMintLimit;`

None of the `initialize()` functions assign `dailyMintLimit`. The `limitDailyMint` modifier (RSETHPoolV3.sol L96–125, identical in the other two contracts) contains the critical check at L119:

```solidity
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
```

With `dailyMintLimit == 0` and `startTimestamp == 0`:
1. `block.timestamp < 0` → `false` (passes the first guard).
2. `getCurrentDay()` returns `(block.timestamp - 0) / 1 days`, a large number greater than `lastMintDay == 0`, so `dailyMintAmount` resets to `0`.
3. `0 + rsETHAmount > 0` → **always true** for any non-zero deposit → reverts with `DailyMintLimitExceeded`.

The value is only set in a separate `reinitialize()` call: `reinitializer(2)` for `RSETHPoolV3` (L179–198) and `RSETHPoolV3WithNativeChainBridge` (L218–237), and `reinitializer(4)` for `RSETHPoolV3ExternalBridge` (L276–295). A `setDailyMintLimit()` setter also exists (RSETHPoolV3.sol L605–611) but requires `DEFAULT_ADMIN_ROLE` and must be invoked manually. Until one of these is called, every `deposit()` call from any user reverts.

## Impact Explanation
All user-facing `deposit()` functions revert for every non-zero amount until an admin intervenes. Users cannot deposit ETH or supported tokens to receive `wrsETH`/`rsETH`. This constitutes **Medium. Temporary freezing of funds** — the core deposit functionality is completely non-operational from deployment until admin action. The freeze is not permanent because `setDailyMintLimit()` can be called by the admin, but until that happens the protocol is inoperable for all depositors.

## Likelihood Explanation
Any fresh deployment or upgrade where the corresponding `reinitialize()` for the daily mint limit is not called atomically with `initialize()` will exhibit this behavior. The multi-step upgrade pattern used across all three contracts (up to `reinitializer(6)` in `RSETHPoolV3ExternalBridge`) creates a realistic window between `initialize()` and the relevant `reinitialize()` call. No attacker action is required — the state is broken by default and affects every unprivileged depositor.

## Recommendation
Initialize `dailyMintLimit` to a sensible non-zero default directly inside `initialize()`, or add a bypass in `limitDailyMint` that skips enforcement when `dailyMintLimit == 0` (treating zero as "no limit configured yet"). Alternatively, require `dailyMintLimit` as a parameter to `initialize()` with a non-zero validation, mirroring the validation already present in `reinitialize()`.

## Proof of Concept
1. Deploy `RSETHPoolV3` proxy and call `initialize(admin, bridger, wrsETH, feeBps, oracle, true)`.
2. Do **not** call `reinitialize(dailyMintLimit, startTimestamp)` or `setDailyMintLimit()`.
3. Any user calls `deposit{value: 1 ether}("ref")`.
4. Inside `limitDailyMint`:
   - `block.timestamp < 0` → `false` (passes).
   - `viewSwapRsETHAmountAndFee(1 ether)` returns `rsETHAmount > 0`.
   - `getCurrentDay()` returns `block.timestamp / 1 days` (large number > `lastMintDay == 0`), so `dailyMintAmount` resets to `0`.
   - `0 + rsETHAmount > dailyMintLimit` → `rsETHAmount > 0` → **always true**.
   - Reverts with `DailyMintLimitExceeded`.
5. All deposits are blocked until admin calls `setDailyMintLimit(nonZeroValue)`.

Foundry test plan: deploy proxy, call `initialize`, skip `reinitialize`, `vm.expectRevert(DailyMintLimitExceeded.selector)`, call `deposit{value: 1 ether}("ref")` — assertion passes, confirming the revert.