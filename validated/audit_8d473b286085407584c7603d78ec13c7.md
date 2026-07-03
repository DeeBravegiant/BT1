The critical fact is confirmed by the grep: `isInitialized2` appears **only** in `UnlockedWithdrawalsInitializer.sol` (3 times — the interface declaration, the modifier, and the interface method body). It does **not** exist anywhere in `LRTWithdrawalManager.sol`.

---

### Title
`onlyBeforeInitialize2` Permanently Reverts Due to Missing `isInitialized2()` on `LRTWithdrawalManager`, Blocking Initialization and Enabling `sweepRemainingAssets` to Drain User Funds — (`contracts/utils/UnlockedWithdrawalsInitializer.sol`)

### Summary

The `onlyBeforeInitialize2` modifier in `UnlockedWithdrawalsInitializer` calls `isInitialized2()` on `LRTWithdrawalManager` via a `try/catch`. However, `LRTWithdrawalManager` does not implement `isInitialized2()`. The EVM reverts with an empty return (unknown selector), the `catch` block fires unconditionally, and the modifier always reverts with `WithdrawalManagerAlreadyInitialized2`. This permanently blocks `processChunk` and `finalizeInitialize2`, making it impossible to seed `unlockedWithdrawalsCount` for existing unlocked withdrawals. As a result, `hasUnlockedWithdrawals` returns `false` for all assets even when real unlocked withdrawals exist, and `sweepRemainingAssets` can drain user-claimable assets.

### Finding Description

**Root cause — missing function:**

`grep` across the entire repository finds `isInitialized2` only in `UnlockedWithdrawalsInitializer.sol`. `LRTWithdrawalManager` never defines it. [1](#0-0) 

**Broken modifier:**

```solidity
modifier onlyBeforeInitialize2() {
    try _withdrawalManager().isInitialized2() returns (bool isInitialized) {
        if (isInitialized) revert WithdrawalManagerAlreadyInitialized2();
    } catch {
        revert WithdrawalManagerAlreadyInitialized2(); // ← always reached
    }
    _;
}
``` [2](#0-1) 

Because `LRTWithdrawalManager` has no `isInitialized2()` selector, the low-level call reverts with empty data, the `catch` block fires, and the modifier unconditionally reverts. Every call to `processChunk` and `finalizeInitialize2` is permanently blocked. [3](#0-2) [4](#0-3) 

**Consequence — `unlockedWithdrawalsCount` stays at zero:**

`initialize2` on `LRTWithdrawalManager` is never called through this initializer, so `unlockedWithdrawalsCount` for all assets remains `0` for any pre-existing unlocked withdrawals. [5](#0-4) 

**`hasUnlockedWithdrawals` returns false:**

```solidity
function hasUnlockedWithdrawals(address asset) public view returns (bool hasUnlocked) {
    return unlockedWithdrawalsCount[asset] > 0;
}
``` [6](#0-5) 

With `unlockedWithdrawalsCount == 0`, this always returns `false` even when real unlocked withdrawals exist.

**`sweepRemainingAssets` drains user funds:**

```solidity
function sweepRemainingAssets(address asset) external nonReentrant onlySupportedAsset(asset) onlyLRTManager ... {
    if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist(); // passes — false positive
    ...
    _transferAsset(asset, treasury, balance); // drains user-claimable assets
}
``` [7](#0-6) 

The LRT Manager, acting in good faith on the protocol's own state, calls `sweepRemainingAssets` and drains assets that users are entitled to claim.

### Impact Explanation

Pre-existing unlocked withdrawal assets held in `LRTWithdrawalManager` can be swept to the treasury while users still hold valid, unlocked withdrawal requests. Users calling `completeWithdrawal` would then find insufficient balance. This is direct theft of user funds at rest.

### Likelihood Explanation

This is the intended upgrade path for existing mainnet deployments. The `UnlockedWithdrawalsInitializer` is deployed and pointed at the live `LRTWithdrawalManager`. The broken modifier fires on the very first call to `processChunk`. No special attacker is needed — the bug is triggered by the normal operator workflow. The LRT Manager calling `sweepRemainingAssets` in good faith (believing the protocol's own `hasUnlockedWithdrawals` view) completes the impact.

### Recommendation

Add `isInitialized2()` to `LRTWithdrawalManager`:

```solidity
function isInitialized2() external view returns (bool) {
    return _initialized >= 2;
}
```

Alternatively, invert the `catch` logic: treat a revert on `isInitialized2()` as "not yet initialized" (i.e., allow the call through) rather than treating it as "already initialized."

### Proof of Concept

```solidity
// 1. Deploy LRTWithdrawalManager (no isInitialized2())
// 2. Deploy UnlockedWithdrawalsInitializer, point at withdrawal manager
// 3. Call processChunk(asset, 100) as operator
//    → reverts with WithdrawalManagerAlreadyInitialized2
// 4. Call finalizeInitialize2() as manager
//    → reverts with WithdrawalManagerAlreadyInitialized2
// 5. unlockedWithdrawalsCount[asset] == 0 for all assets
// 6. hasUnlockedWithdrawals(asset) == false (even with real unlocked withdrawals)
// 7. sweepRemainingAssets(asset) as LRT Manager succeeds, drains user funds
```

### Citations

**File:** contracts/utils/UnlockedWithdrawalsInitializer.sol (L19-19)
```text
    function isInitialized2() external view returns (bool);
```

**File:** contracts/utils/UnlockedWithdrawalsInitializer.sol (L61-68)
```text
    modifier onlyBeforeInitialize2() {
        try _withdrawalManager().isInitialized2() returns (bool isInitialized) {
            if (isInitialized) revert WithdrawalManagerAlreadyInitialized2();
        } catch {
            revert WithdrawalManagerAlreadyInitialized2();
        }
        _;
    }
```

**File:** contracts/utils/UnlockedWithdrawalsInitializer.sol (L75-82)
```text
    function processChunk(
        address asset,
        uint256 maxIterations
    )
        external
        onlyLRTOperator
        onlyBeforeInitialize2
        returns (uint256 processed, uint256 added)
```

**File:** contracts/utils/UnlockedWithdrawalsInitializer.sol (L112-123)
```text
    function finalizeInitialize2() external onlyLRTManager onlyBeforeInitialize2 {
        if (!isAssetComplete(_ethX()) || !isAssetComplete(_stETH()) || !isAssetComplete(_eth())) {
            revert PendingWithdrawalsExist();
        }

        uint256 countETHx = unlockedCount[_ethX()];
        uint256 countSTETH = unlockedCount[_stETH()];
        uint256 countETH = unlockedCount[_eth()];

        _withdrawalManager().initialize2(countETHx, countSTETH, countETH);
        emit Finalized(countETHx, countSTETH, countETH);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L109-121)
```text
    function initialize2(
        uint256 unlockedWithdrawalsCountETHx,
        uint256 unlockedWithdrawalsCountSTETH,
        uint256 unlockedWithdrawalsCountETH
    )
        external
        reinitializer(2)
        onlyRole(LRTConstants.UNLOCKED_WITHDRAWAL_INITIALIZER)
    {
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ETHX_TOKEN)] = unlockedWithdrawalsCountETHx;
        unlockedWithdrawalsCount[LRTConstants.ETH_TOKEN] = unlockedWithdrawalsCountETH;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L395-414)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L629-631)
```text
    function hasUnlockedWithdrawals(address asset) public view returns (bool hasUnlocked) {
        return unlockedWithdrawalsCount[asset] > 0;
    }
```
