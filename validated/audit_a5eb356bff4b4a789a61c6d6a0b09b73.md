Looking at the actual code to verify each component of the claim.

All code components are confirmed. The claim checks out on every point:

1. `ExtensionCalling._beforeAddLiquidity` correctly passes both `sender` (actual caller) and `owner` (position recipient) to the extension — the data is available.
2. `DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (unnamed first parameter) and gates only on `owner`.
3. `MetricOmmPool.addLiquidity` has no `msg.sender == owner` guard — the operator pattern is explicit.
4. `SwapAllowlistExtension.beforeSwap` correctly checks `sender`, confirming the asymmetry.
5. The `IDepositAllowlistExtension` interface uses the term "depositor" throughout (`allowedDepositor`, `setAllowedToDeposit`, `isAllowedToDeposit`), indicating the intent is to gate the actual depositor, not the position recipient.

---

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` receives both `sender` (the actual `msg.sender` of `addLiquidity`) and `owner` (the position recipient) but silently discards `sender` and gates only on `owner`. Because `MetricOmmPool.addLiquidity` enforces no `msg.sender == owner` constraint (the operator pattern), any non-allowlisted caller can bypass the deposit allowlist by supplying an allowlisted address as `owner`, paying tokens via their own callback, and having shares credited to that address.

## Finding Description
`MetricOmmPool.addLiquidity` (L182–196) accepts an arbitrary `owner` address with no ownership check and calls `_beforeAddLiquidity(msg.sender, owner, ...)`. `ExtensionCalling._beforeAddLiquidity` (L88–99) correctly encodes both `sender` and `owner` and forwards them to the extension. However, `DepositAllowlistExtension.beforeAddLiquidity` (L32–42) declares the first parameter unnamed, discards it entirely, and evaluates only `allowedDepositor[msg.sender][owner]` — where `msg.sender` is the pool. The effective check is `allowedDepositor[pool][owner]`, not `allowedDepositor[pool][sender]`.

**Exploit path:**
1. Pool is deployed with `DepositAllowlistExtension`; Alice is allowlisted (`allowedDepositor[pool][alice] = true`); Bob is not.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` invokes `metricOmmModifyLiquidityCallback` on Bob; Bob pays the tokens.
5. Shares are credited to `(alice, salt, bin)` in `_positionBinShares`.
6. Bob has deposited into a restricted pool without being allowlisted.

`SwapAllowlistExtension.beforeSwap` (L31–41) correctly checks `sender`, confirming the asymmetry is a defect, not a design choice. The `IDepositAllowlistExtension` interface names the gated party "depositor" throughout (`allowedDepositor`, `setAllowedToDeposit`, `isAllowedToDeposit`), further confirming the intent is to gate the actual depositor (sender), not the position recipient (owner).

## Impact Explanation
The pool admin's deposit access control boundary is fully bypassed. Any unprivileged caller can deposit into a pool protected by `DepositAllowlistExtension` — including pools configured for KYC, regulatory compliance, or curated LP sets — by routing through an allowlisted `owner` address. This is a direct admin-boundary break: an unprivileged path circumvents a pool-admin-configured guard with no fund loss to the attacker (they pay tokens and receive shares via the owner address they chose).

## Likelihood Explanation
Exploitation requires only a direct call to `pool.addLiquidity` with an allowlisted address as `owner`. No special privileges, flash loans, or complex setup are needed. The `allowedDepositor` mapping is public, so any observer can identify allowlisted addresses. Any caller who can implement `metricOmmModifyLiquidityCallback` can exploit this immediately and repeatably.

## Recommendation
Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual depositor/caller) instead of `owner` (the position recipient), mirroring the correct pattern in `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept
```solidity
// Setup: pool has DepositAllowlistExtension; Alice is allowlisted, Bob is not
depositExtension.setAllowedToDeposit(address(pool), alice, true);
// Bob is NOT allowlisted

// Bob calls addLiquidity with alice as owner
// Bob implements metricOmmModifyLiquidityCallback to pay tokens
vm.prank(bob);
pool.addLiquidity(
    alice,        // owner — allowlisted, passes extension check
    salt,
    deltas,
    callbackData, // Bob's callback pays the tokens
    extensionData
);

// Result: Bob deposited into a restricted pool
uint256 aliceShares = positionBinShares[keccak256(abi.encode(alice, salt, bin))];
assertGt(aliceShares, 0); // passes — allowlist bypassed
```