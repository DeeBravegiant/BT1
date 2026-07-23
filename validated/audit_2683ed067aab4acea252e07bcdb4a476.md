### Title
`DepositAllowlistExtension` Allowlist Bypassed via Whitelisted `owner` Argument — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (LP-share recipient) instead of the `sender` parameter (the actual `msg.sender` of `addLiquidity`). Because `addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any unprivileged address can bypass the deposit allowlist by calling `addLiquidity` with a whitelisted `owner`, paying the tokens themselves while the whitelisted address receives the LP shares.

---

### Finding Description

`MetricOmmPool.addLiquidity` forwards both the real caller and the intended LP owner to the extension layer:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` encodes both values and calls the extension:

```solidity
// ExtensionCalling.sol:95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but silently discards it (unnamed `address`), checking only `owner`:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the pool address. So the guard reduces to: *"Is `owner` on the allowlist for this pool?"* — the actual caller is never examined.

This is structurally identical to the `DIAWhitelistedStaking` bug: the whitelist check is applied to the beneficiary address, not to the initiating party.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swap caller) and ignores `recipient`:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The two sibling extensions are inconsistent: `SwapAllowlistExtension` gates the caller; `DepositAllowlistExtension` gates the LP-share recipient.

---

### Impact Explanation

The deposit allowlist is rendered completely ineffective. Any address — regardless of allowlist status — can add liquidity to a restricted pool by supplying any whitelisted address as `owner`. The attacker pays the tokens (via the modify-liquidity callback) and the whitelisted address receives the LP shares. Concrete consequences:

- **Allowlist bypass**: pools intended for permissioned LPs only accept deposits from unauthorized parties.
- **Forced LP positions**: a whitelisted address receives LP shares it never requested, which can be used to grief it (e.g., locking capital in bins at unfavorable prices that the victim must later unwind).
- **Pool state manipulation**: an unprivileged actor can shift the pool's liquidity distribution across bins, affecting swap prices and fee accrual for existing LPs.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `addLiquidity` with a whitelisted `owner`. The only cost is the token payment, which the attacker controls (they can choose the smallest valid deposit). Likelihood is **High**.

---

### Recommendation

Mirror the pattern used by `SwapAllowlistExtension`: check `sender` (the actual caller) rather than `owner` (the LP-share recipient).

```solidity
// DepositAllowlistExtension.sol — corrected
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantics are to gate by LP-share recipient (`owner`), the NatSpec and admin tooling must be updated to make that explicit, and the operator pattern must be documented as intentionally unrestricted.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached and whitelists only `alice`.
2. `bob` (not whitelisted) holds token0 and token1 and implements `IMetricOmmModifyLiquidityCallback`.
3. `bob` calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
4. Inside `beforeAddLiquidity`, `msg.sender` = pool, `owner` = `alice`.
   - `allowedDepositor[pool][alice]` = `true` → check passes.
5. `LiquidityLib.addLiquidity` mints shares under the key `(alice, salt, binIdx)`.
6. The modify-liquidity callback fires on `bob`; `bob` transfers tokens to the pool.
7. Result: `bob` (unprivileged) has added liquidity to a restricted pool; `alice` now holds LP shares she never requested; the allowlist is bypassed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
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
