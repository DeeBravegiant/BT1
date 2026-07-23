### Title
User-Supplied `salt` in `CREATE2` Pool Deployment Enables Address Collision Attack to Drain LP Funds — (`metric-core/contracts/MetricOmmPoolDeployer.sol`)

---

### Summary

`MetricOmmPoolFactory.createPool()` accepts a caller-supplied `bytes32 salt` and passes it unmodified to `MetricOmmPoolDeployer.deploy()`, which uses it verbatim in `new MetricOmmPool{salt: params.salt}(...)`. Because the attacker also controls all constructor arguments (token0, token1, priceProvider, admin, etc.), they have full control over both inputs that determine the CREATE2 address. A meet-in-the-middle brute-force can find a collision between an undeployed pool address and an attacker-controlled contract, allowing the attacker to pre-set unlimited token allowances at that address before the pool is deployed there, then drain all LP deposits.

---

### Finding Description

The `PoolParameters` struct exposes `salt` as a plain `bytes32` field with no restrictions: [1](#0-0) 

`_validatePoolParameters()` validates tokens, fees, admin, and price provider, but never touches `salt`: [2](#0-1) 

The factory forwards `params.salt` directly to the deployer without mixing in any factory-controlled entropy (no `msg.sender`, no nonce, no block data): [3](#0-2) 

The deployer uses it verbatim in CREATE2: [4](#0-3) 

The resulting CREATE2 address is:

```
keccak256(0xff ++ deployer_address ++ params.salt ++ keccak256(initcode))
```

where `deployer_address` is fixed, `params.salt` is fully attacker-controlled, and `keccak256(initcode)` is also attacker-controlled because the attacker supplies all constructor arguments (token0, token1, priceProvider, admin, etc.). This gives the attacker complete control over the pool's deployed address.

**Attack steps:**

**Tx 1 (collision setup):**
- Brute-force `params.salt` values (fixing constructor args) to enumerate ~2^80 candidate pool addresses; store in a Bloom filter.
- Separately brute-force a CREATE2 salt for an attacker-owned deployer contract to find a collision with any address in the stored set.
- In a single transaction: deploy the attack contract to `0xCOLLIDED`, call `token0.approve(attacker, type(uint256).max)` and `token1.approve(attacker, type(uint256).max)` from `0xCOLLIDED`, then `selfdestruct` (valid post-Dencun when contract was created in the same tx per EIP-6780).

After Tx 1: `0xCOLLIDED` has no code but holds unlimited token allowances for the attacker.

**Tx 2 (drain):**
- Call `createPool()` with the matching `salt` and constructor args → pool deploys to `0xCOLLIDED`.
- LPs call `addLiquidity()` → token0 and token1 flow into `0xCOLLIDED`.
- Attacker calls `transferFrom(0xCOLLIDED, attacker, balance)` for both tokens using the pre-set allowances.

The pool contract at `0xCOLLIDED` holds no record of the allowances (they are ERC-20 storage on the token contracts, not on the pool), so the pool's own accounting is unaffected — but the underlying token balances are gone, making the pool insolvent.

---

### Impact Explanation

Complete draining of all token0 and token1 deposited by LPs into any targeted pool. The pool's `binState.token0BalanceScaled` / `token1BalanceScaled` accounting will show LP claims that the pool can no longer honour, causing full insolvency. Every pool deployed by this factory is a potential target; the attacker can target the highest-TVL pool. [5](#0-4) 

---

### Likelihood Explanation

The attack requires a meet-in-the-middle collision over a 160-bit address space. With ~2^80 hashes on each side the success probability exceeds 86%. The Bitcoin network alone achieves ~6×10^20 hashes/second (~2^80 in ~33 minutes). A fraction of that hashrate suffices. The cost has been estimated at a few million USD — easily offset by a DeFi pool with tens of millions in TVL. Likelihood increases over time as hardware improves and as more pools accumulate value.

---

### Recommendation

Remove the caller's ability to determine the pool address. Two options:

1. **Use `CREATE` instead of `CREATE2`** in `MetricOmmPoolDeployer.deploy()`. The address is then determined by the deployer's address and its internal nonce, which the attacker cannot control.

2. **Mix factory-controlled entropy into the salt** before passing it to CREATE2, e.g.:
   ```solidity
   bytes32 guardedSalt = keccak256(abi.encode(msg.sender, params.salt, nextPoolIdx));
   ```
   This prevents the attacker from pre-computing the pool address because `nextPoolIdx` is not known until the transaction executes, and `msg.sender` is not freely chosen.

Option 1 is simpler and eliminates the attack surface entirely. Option 2 preserves deterministic address pre-computation for integrators but requires that the guarded salt is not predictable before the transaction lands.

---

### Proof of Concept

```
Precondition: MetricOmmPoolDeployer is deployed at known address D.
              Attacker controls EOA A and a CREATE2 factory contract F.

Step 1 – Enumerate pool-side addresses:
  For salt_i in [0, 2^80):
    addr_i = keccak256(0xff ++ D ++ salt_i ++ POOL_INIT_CODE_HASH)[12:]
    insert addr_i into Bloom filter B

Step 2 – Find collision:
  For salt_j in [0, 2^80):
    addr_j = keccak256(0xff ++ F ++ salt_j ++ ATTACK_INIT_CODE_HASH)[12:]
    if addr_j in B:
      record (salt_i_matching, salt_j)
      break

Step 3 – Tx 1 (single transaction via F):
  F.deployAndSelfDestruct(salt_j):
    deploy AttackContract to addr_j (== addr_i)
    AttackContract.constructor():
      token0.approve(A, type(uint256).max)
      token1.approve(A, type(uint256).max)
      selfdestruct(A)

Step 4 – Tx 2:
  factory.createPool(PoolParameters{salt: salt_i_matching, ...})
  // Pool deploys to addr_i == addr_j
  // LPs add liquidity; tokens accumulate at addr_i

Step 5 – Drain:
  token0.transferFrom(addr_i, A, token0.balanceOf(addr_i))
  token1.transferFrom(addr_i, A, token1.balanceOf(addr_i))
```

The coded PoC pattern (deploy + approve + selfdestruct in one tx, then redeploy at same address) is identical to the one validated on Remix/Holesky in the referenced Arcadia report and relies solely on standard EVM behaviour confirmed by EIP-3607 and EIP-6780.

### Citations

**File:** metric-core/contracts/types/FactoryOperation.sol (L35-35)
```text
  bytes32 salt;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L180-204)
```text
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L548-563)
```text
  function _validatePoolParameters(PoolParameters calldata params) internal view {
    if (params.token0 == address(0) || params.token1 == address(0) || params.token0 == params.token1) {
      revert InvalidTokenConfig();
    }
    if (params.admin == address(0)) revert InvalidAdmin();
    _validatePriceProvider(params.token0, params.token1, params.priceProvider);
    if (params.adminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    if (spreadProtocolFeeE6 > maxProtocolSpreadFeeE6) revert ProtocolFeeTooHigh();
    if (protocolNotionalFeeE8 > maxProtocolNotionalFeeE8) revert ProtocolFeeTooHigh();
    if (params.adminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (params.adminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
    if (params.initialAmount0PerShareE18 == 0 || params.initialAmount1PerShareE18 == 0) {
      revert InvalidInitialAmount();
    }
    if (params.minimalMintableLiquidity == 0) revert InvalidMinimalMintableLiquidity();
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L109-120)
```text
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
          if (amount0Scaled > 0) {
            totalToken0ToAddScaled += amount0Scaled;
            binState.token0BalanceScaled = (uint256(binState.token0BalanceScaled) + amount0Scaled).toUint104();
          }
          if (amount1Scaled > 0) {
            totalToken1ToAddScaled += amount1Scaled;
            binState.token1BalanceScaled = (uint256(binState.token1BalanceScaled) + amount1Scaled).toUint104();
          }
          binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
```
