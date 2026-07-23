Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` validates `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary

`DepositAllowlistExtension` is intended to gate `addLiquidity` by the depositor (the address paying tokens), but its `beforeAddLiquidity` hook silently discards the `sender` argument and validates `owner` (the position beneficiary) instead. Because `MetricOmmPool.addLiquidity` accepts any `owner` address without requiring `msg.sender == owner`, any unprivileged caller can bypass the allowlist by supplying an already-allowed address as `owner`, paying tokens themselves while the allowed address receives the position.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position beneficiary to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed (discarded), then checks only `owner`: [3](#0-2) 

The contract's NatDoc and storage mapping (`allowedDepositor`) both express the intent to gate by depositor (payer), but the implementation gates by position beneficiary. Since `addLiquidity` imposes no `msg.sender == owner` constraint, an attacker can freely choose any allowed address as `owner`. The `removeLiquidity` guard (`msg.sender != owner`) only prevents the attacker from withdrawing the position they funded — it does not prevent the allowlist bypass itself: [4](#0-3) 

## Impact Explanation

A pool admin who deploys `DepositAllowlistExtension` to restrict deposits to a curated set of LPs (KYC'd market makers, whitelisted vaults, regulated counterparties) receives no protection. Any unprivileged address can call `pool.addLiquidity(allowedAddress, salt, ...)`, pass the extension check, and inject liquidity into the restricted pool. The allowed address receives an unsolicited position it can immediately drain via `removeLiquidity`. The pool admin's access-control boundary is completely nullified — unauthorized liquidity enters the pool, violating the admin's composition or regulatory intent. This constitutes an admin-boundary break by an unprivileged path, a recognized allowed impact.

## Likelihood Explanation

- `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` requirement, so the attack path is unconditionally open whenever the extension is active.
- `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` only checks `owner != address(0)`, not `owner == msg.sender`, providing an additional entry point.
- The allowlist is public on-chain storage; any observer can read a valid `owner` address.
- No special privilege, flash loan, or oracle manipulation is required — a single direct call suffices.
- The attack is repeatable at any time and by any address.

## Recommendation

Replace the discarded first parameter with a named `sender` and validate it instead of `owner`, aligning the check with the documented intent and the `allowedDepositor` mapping name:

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

```
Setup:
  pool uses DepositAllowlistExtension
  allowAllDepositors[pool] = false
  allowedDepositor[pool][alice] = true   // alice is the only allowed LP
  bob is NOT in the allowlist

Attack:
  bob calls pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)
    → _beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)
    → extension.beforeAddLiquidity(bob /*discarded*/, alice /*checked*/, ...)
    → allowedDepositor[pool][alice] == true → passes
    → LiquidityLib.addLiquidity credits shares to (alice, salt, bin)
    → callback pulls tokens from bob
    → alice now holds a position she can removeLiquidity to drain

Result:
  bob bypassed the allowlist and injected liquidity into a restricted pool.
  alice receives tokens she did not deposit and can immediately withdraw them.
  pool admin's access control is nullified for all future deposits.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
