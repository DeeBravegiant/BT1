### Title
Factory Owner Can Permanently Strand Protocol Fees and Lock Protocol-Paused Pools via Inherited `renounceOwnership()` - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

### Summary

`MetricOmmPoolFactory` inherits OpenZeppelin's `Ownable2Step`, which in turn inherits `Ownable`. The `Ownable.renounceOwnership()` function is not overridden in `MetricOmmPoolFactory`. If called, it sets `owner` to `address(0)`, permanently disabling all `onlyOwner` functions. This strands all protocol fees already transferred to the factory contract and makes any pool at protocol-pause level 2 permanently unswappable.

### Finding Description

`MetricOmmPoolFactory` is declared as:

```solidity
contract MetricOmmPoolFactory is Ownable2Step, IMetricOmmPoolFactory, ReentrancyGuardTransient {
``` [1](#0-0) [2](#0-1) 

OpenZeppelin's `Ownable2Step` inherits `Ownable` and does **not** override `renounceOwnership()`. The function is therefore callable by the current owner and immediately sets `owner = address(0)` with no two-step protection and no reversal path.

**Impact path 1 — Protocol fees permanently stuck:**

Every time `collectFees` is triggered on a pool, the protocol's share of both spread and notional fees is pushed directly to the factory address:

```solidity
if (totalFee0ToProtocol > 0) {
    transferToken0(FACTORY, totalFee0ToProtocol);
}
if (totalFee1ToProtocol > 0) {
    transferToken1(FACTORY, totalFee1ToProtocol);
}
``` [3](#0-2) 

The only way to retrieve these tokens from the factory is via `collectTokens` or `collectEth`, both of which are `onlyOwner`: [4](#0-3) [5](#0-4) 

After `renounceOwnership()`, these calls revert permanently. All protocol fees already in the factory, and all future fees collected from any pool, are irrecoverable.

**Impact path 2 — Protocol-paused pools permanently frozen for swaps:**

The factory exposes two pause levels. Level 2 is the protocol-level pause, settable only by the owner: [6](#0-5) 

The pool's `swap` function enforces `whenNotPaused`, which reverts when `pauseLevel != 0`: [7](#0-6) 

`protocolUnpausePool` is `onlyOwner` and transitions level 2 → 1. The pool admin's `unpausePool` only handles level 1 → 0. There is no path from level 2 to any unpaused state without the factory owner. After `renounceOwnership()`, any pool at level 2 is permanently swap-frozen.

**No guard exists:** A grep across all `.sol` files confirms `renounceOwnership` is never overridden or blocked anywhere in the codebase. The pool admin transfer path does guard against a zero admin (`if (newAdmin == address(0)) revert InvalidAdmin()`), but no equivalent guard exists for the factory owner role. [8](#0-7) 

### Impact Explanation

- **Protocol fee loss:** All token0 and token1 protocol fees routed to the factory via `collectFees` become permanently unrecoverable. This is a direct, quantifiable loss of protocol revenue with no recovery path.
- **Broken swap functionality:** Any pool placed at protocol-pause level 2 (e.g., during a fraud or emergency) can never be unpaused. Traders cannot swap; the pool is permanently degraded even after the underlying emergency is resolved. LPs can still remove liquidity (no `whenNotPaused` on `removeLiquidity`), but the pool's core swap function is permanently broken.

### Likelihood Explanation

The trigger requires the factory owner to call `renounceOwnership()`. This could occur accidentally (mistaking it for a two-step transfer initiation), via a compromised owner key, or through a governance mistake. The function is publicly visible in the ABI and requires no special conditions. The consequence is irreversible on-chain.

### Recommendation

Override `renounceOwnership()` in `MetricOmmPoolFactory` to unconditionally revert:

```solidity
function renounceOwnership() public pure override {
    revert OwnershipCannotBeRenounced();
}
```

This mirrors the fix applied in Connext PR 2412 and eliminates the irreversible ownership-loss vector without affecting the two-step transfer flow provided by `Ownable2Step`.

### Proof of Concept

1. Deploy `MetricOmmPoolFactory` with `initialOwner = alice`.
2. Several pools accumulate protocol fees; `collectPoolFees` is called, routing token0/token1 to the factory.
3. Alice calls `MetricOmmPoolFactory.renounceOwnership()` (inherited, not overridden).
4. `owner()` returns `address(0)`.
5. Call `collectTokens(token0, alice, 0)` → reverts `OwnableUnauthorizedAccount(address(0))`.
6. Call `protocolUnpausePool(pool)` on any level-2-paused pool → same revert.
7. All protocol fees in the factory are permanently stuck; all level-2-paused pools are permanently swap-frozen. [9](#0-8) [10](#0-9)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L9-9)
```text
import {Ownable2Step, Ownable} from "@openzeppelin/contracts/access/Ownable2Step.sol";
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L35-35)
```text
contract MetricOmmPoolFactory is Ownable2Step, IMetricOmmPoolFactory, ReentrancyGuardTransient {
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L104-118)
```text
  constructor(address initialOwner) Ownable(initialOwner) {
    maxProtocolSpreadFeeE6 = HARD_MAX_SPREAD_FEE_E6;
    maxAdminSpreadFeeE6 = HARD_MAX_SPREAD_FEE_E6;
    maxProtocolNotionalFeeE8 = HARD_MAX_NOTIONAL_FEE_E8;
    maxAdminNotionalFeeE8 = HARD_MAX_NOTIONAL_FEE_E8;
    spreadProtocolFeeE6 = 0;
    protocolNotionalFeeE8 = 0;
    nextPoolIdx = 1;

    emit FeeCapsUpdated(
      HARD_MAX_SPREAD_FEE_E6, HARD_MAX_SPREAD_FEE_E6, HARD_MAX_NOTIONAL_FEE_E8, HARD_MAX_NOTIONAL_FEE_E8
    );
    emit SpreadProtocolFeeDefaultUpdated(0, 0);
    emit ProtocolNotionalFeeDefaultUpdated(0, 0);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L262-269)
```text
  function collectTokens(address token, address to, uint256 amount) external override onlyOwner {
    uint256 balance = IERC20(token).balanceOf(address(this));
    uint256 amountToCollect = amount == 0 ? balance : amount;
    if (amountToCollect > 0) {
      IERC20(token).safeTransfer(to, amountToCollect);
      emit TokensCollected(token, to, amountToCollect);
    }
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L272-280)
```text
  function collectEth(address payable to, uint256 amount) external override onlyOwner {
    uint256 balance = address(this).balance;
    uint256 amountToCollect = amount == 0 ? balance : amount;
    if (amountToCollect > 0) {
      (bool success,) = to.call{value: amountToCollect}("");
      require(success, "ETH transfer failed");
      emit TokensCollected(address(0), to, amountToCollect);
    }
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L379-389)
```text
  function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L392-403)
```text
  function protocolPausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0 && cur != 1) revert InvalidPauseTransition(cur, 2);
    IMetricOmmPoolFactoryActions(pool).setPause(2);
  }

  /// @inheritdoc IMetricOmmPoolFactoryOwner
  function protocolUnpausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 2) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L510-514)
```text
  function proposePoolAdminTransfer(address pool, address newAdmin) external override nonReentrant onlyPoolAdmin(pool) {
    if (newAdmin == address(0)) revert InvalidAdmin();
    if (newAdmin == poolAdmin[pool]) revert InvalidAdmin();
    pendingPoolAdmin[pool] = newAdmin;
    emit PoolAdminTransferProposed(pool, poolAdmin[pool], newAdmin);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L422-427)
```text
      if (totalFee0ToProtocol > 0) {
        transferToken0(FACTORY, totalFee0ToProtocol);
      }
      if (totalFee1ToProtocol > 0) {
        transferToken1(FACTORY, totalFee1ToProtocol);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```
