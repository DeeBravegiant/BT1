### Title
`owner` (user-controlled) checked instead of `sender` (authenticated caller) in `DepositAllowlistExtension.beforeAddLiquidity`, allowing any unprivileged address to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the user-supplied `owner` parameter instead of the authenticated `sender` parameter (the actual `msg.sender` of the `addLiquidity` call). Because `owner` is freely chosen by the caller, any address can bypass the allowlist by setting `owner` to an already-allowed address, completely defeating the admin-configured access control.

---

### Finding Description

The `IMetricOmmExtensions.beforeAddLiquidity` interface passes two address arguments: `sender` (position 1, the authenticated `msg.sender` of the pool's `addLiquidity` call) and `owner` (position 2, the user-supplied position owner). [1](#0-0) 

The pool encodes and dispatches both: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (unnamed first parameter) and gates on `owner` instead: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap`, by contrast, correctly names and checks `sender`: [4](#0-3) 

Because `addLiquidity` explicitly supports the operator pattern (`msg.sender` pays, `owner` receives the position), `owner` is entirely caller-controlled: [5](#0-4) 

An attacker calls `pool.addLiquidity(allowedAddress, salt, deltas, callbackData, extensionData)`. The pool passes `(msg.sender=attacker, owner=allowedAddress)` to the extension. The extension evaluates `allowedDepositor[pool][allowedAddress]` → `true`, so the check passes. The attacker's callback pays the tokens; the LP position is minted under `allowedAddress`.

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict who may provide liquidity (e.g., KYC-gated pools, curated market-maker sets, or regulatory compliance). With this bug the control is entirely inoperative: any unprivileged address can deposit into a restricted pool by nominating any already-allowed address as `owner`. The LP shares are credited to that allowed address (not the attacker), so the attacker loses tokens, but:

- The allowlist invariant is broken — the admin-configured access boundary is bypassed by an unprivileged path.
- The attacker can force LP positions onto allowed addresses without their consent (griefing).
- Pools relying on the allowlist for compliance or economic isolation are exposed to unrestricted liquidity injection.

This matches the impact gate category: **Admin-boundary break — factory/pool admin role checks bypassed by an unprivileged path.**

---

### Likelihood Explanation

- Trigger requires only a standard `addLiquidity` call with `owner` set to any address already on the allowlist.
- No special privileges, flash loans, or price manipulation needed.
- The allowed address list is readable on-chain (`allowedDepositor` is a public mapping).
- Any attacker who can observe the allowlist can exploit this immediately.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

```solidity
// Before (vulnerable):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`sender` is the authenticated `msg.sender` of the pool's `addLiquidity` call and cannot be spoofed by the caller.

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `allowedLP` is on the allowlist.
// allowedDepositor[pool][allowedLP] = true

// Attacker (not on allowlist) calls:
pool.addLiquidity(
    allowedLP,          // owner = allowed address (user-controlled)
    salt,
    deltas,
    callbackData,       // attacker's router pays tokens
    extensionData
);

// Extension evaluates: allowedDepositor[pool][allowedLP] == true → passes
// Attacker's callback settles the token debt
// LP shares minted under allowedLP — deposit allowlist fully bypassed
```

The attacker spends tokens but successfully injects liquidity into a pool that should have rejected them. The `allowedLP` address receives an LP position it never requested.

### Citations

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```
