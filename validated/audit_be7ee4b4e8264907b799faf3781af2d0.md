### Title
Pending Withdrawal Requests Permanently Frozen After `LRTWithdrawalManager` Upgrade - (File: contracts/LRTUnstakingVault.sol)

### Summary
When a user initiates a withdrawal via `LRTWithdrawalManager.initiateWithdrawal()`, their rsETH is transferred to the old `LRTWithdrawalManager` and a withdrawal request is recorded. If the admin subsequently upgrades the withdrawal manager by updating `lrtConfig`'s `withdrawManager` address, the old `LRTWithdrawalManager` can no longer call `LRTUnstakingVault.redeem()` because the `onlyLRTWithdrawalManager` modifier dynamically resolves the current address from `lrtConfig`. The pending withdrawal requests — and the rsETH locked inside the old contract — become permanently unrecoverable.

### Finding Description

`LRTUnstakingVault.redeem()` is gated by the `onlyLRTWithdrawalManager` modifier:

```solidity
modifier onlyLRTWithdrawalManager() {
    if (msg.sender != lrtConfig.withdrawManager()) {
        revert CallerNotLRTWithdrawalManager();
    }
    _;
}
``` [1](#0-0) 

This modifier resolves the authorized caller **at call time** from `lrtConfig`, not at deployment time. The `redeem()` function is the only path through which the `LRTWithdrawalManager` pulls assets from the vault to pay users:

```solidity
function redeem(address asset, uint256 amount) external nonReentrant onlyLRTWithdrawalManager {
    if (asset == LRTConstants.ETH_TOKEN) {
        ILRTWithdrawalManager(msg.sender).receiveFromLRTUnstakingVault{ value: amount }();
    } else {
        IERC20(asset).safeTransfer(msg.sender, amount);
    }
}
``` [2](#0-1) 

When a user calls `initiateWithdrawal()`, their rsETH is pulled into the old `LRTWithdrawalManager` and a `WithdrawalRequest` is stored:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
``` [3](#0-2) 

Both `unlockQueue` and `completeWithdrawal` ultimately call `unstakingVault.redeem()`:

```solidity
unstakingVault.redeem(asset, assetAmountUnlocked);
``` [4](#0-3) 

`LRTConfig` acts as the centralized registry for contract addresses, and the admin can update `withdrawManager` at any time. Once updated, the old `LRTWithdrawalManager` fails the `onlyLRTWithdrawalManager` check on every call to `redeem()`, permanently blocking all pending withdrawal completions. The new `LRTWithdrawalManager` has no knowledge of the old contract's `withdrawalRequests` mapping, so those requests cannot be migrated or replayed. [5](#0-4) 

### Impact Explanation

**Critical — Permanent freezing of funds.**

Users who have called `initiateWithdrawal()` before the upgrade have:
1. Their rsETH locked inside the old `LRTWithdrawalManager` (transferred in, never returned).
2. Their corresponding asset entitlement locked inside `LRTUnstakingVault` (committed via `assetsCommitted[asset]`), unreachable because `redeem()` reverts.

There is no escape hatch, no admin function to recover rsETH from the old contract, and no migration path for `withdrawalRequests` state. [6](#0-5) 

### Likelihood Explanation

**Medium.** The protocol is explicitly upgradeable and has already undergone multiple reinitializations (`initialize2`, `initialize3`). A future upgrade to `LRTWithdrawalManager` — e.g., to add Aave integration, fix a bug, or add new asset support — would trigger this freeze for any user with a pending withdrawal at upgrade time. No attacker action is required; the freeze is a side-effect of a routine admin upgrade. [7](#0-6) 

### Recommendation

1. **Drain-before-upgrade check**: Before updating `withdrawManager` in `LRTConfig`, verify that `nextLockedNonce[asset] == nextUnusedNonce[asset]` for all assets (i.e., no pending requests exist).
2. **Emergency withdrawal on old contract**: Add a function to the old `LRTWithdrawalManager` that allows users to reclaim their locked rsETH if the contract is no longer the active withdrawal manager (i.e., `lrtConfig.withdrawManager() != address(this)`).
3. **State migration**: Include a migration step in the upgrade script that replays all open `withdrawalRequests` into the new contract before switching the `withdrawManager` pointer in `LRTConfig`.

### Proof of Concept

```
1. Alice calls LRTWithdrawalManager_v1.initiateWithdrawal(stETH, 1e18, "ref")
   → 1e18 rsETH transferred from Alice to LRTWithdrawalManager_v1
   → withdrawalRequests[requestId] = {rsETHUnstaked: 1e18, expectedAssetAmount: X, ...}
   → assetsCommitted[stETH] += X

2. Admin deploys LRTWithdrawalManager_v2 and calls:
   lrtConfig.setContract(LRT_WITHDRAWAL_MANAGER, address(LRTWithdrawalManager_v2))

3. Alice calls LRTWithdrawalManager_v1.completeWithdrawal(stETH, "ref")
   → internally calls: unstakingVault.redeem(stETH, X)
   → LRTUnstakingVault checks: msg.sender (LRTWithdrawalManager_v1) != lrtConfig.withdrawManager() (LRTWithdrawalManager_v2)
   → REVERT: CallerNotLRTWithdrawalManager

4. Alice's 1e18 rsETH is permanently locked in LRTWithdrawalManager_v1.
   X stETH is permanently locked in LRTUnstakingVault (committed but unredeemable).
   LRTWithdrawalManager_v2 has no record of Alice's request.
``` [1](#0-0) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTUnstakingVault.sol (L54-59)
```text
    modifier onlyLRTWithdrawalManager() {
        if (msg.sender != lrtConfig.withdrawManager()) {
            revert CallerNotLRTWithdrawalManager();
        }
        _;
    }
```

**File:** contracts/LRTUnstakingVault.sol (L99-105)
```text
    function redeem(address asset, uint256 amount) external nonReentrant onlyLRTWithdrawalManager {
        if (asset == LRTConstants.ETH_TOKEN) {
            ILRTWithdrawalManager(msg.sender).receiveFromLRTUnstakingVault{ value: amount }();
        } else {
            IERC20(asset).safeTransfer(msg.sender, amount);
        }
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L44-53)
```text
    // Mapping from a unique request identifier to its corresponding withdrawal request
    mapping(bytes32 requestId => WithdrawalRequest) public withdrawalRequests;

    // Maps each asset to user addresses, pointing to an ordered list of their withdrawal request nonces.
    // Utilizes a double-ended queue for efficient management and removal of initial requests.
    mapping(address asset => mapping(address user => DoubleEndedQueue.Uint256Deque requestNonces)) public
        userAssociatedNonces;

    // Asset amount committed to be withdrawn by users.
    mapping(address asset => uint256 amount) public assetsCommitted;
```

**File:** contracts/LRTWithdrawalManager.sol (L109-129)
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

    /// @notice Initializes unlocked withdrawals count for sfrxETH for legacy purposes
    /// @dev This function will be removed in a future version
    /// @param unlockedWithdrawalsCountSFRXETH The remaining unlocked withdrawals count for sfrxETH
    function initialize3(uint256 unlockedWithdrawalsCountSFRXETH) external reinitializer(3) onlyLRTManager {
        address sfrxETHAddress = 0xac3E018457B222d93114458476f3E3416Abbe38F;
        unlockedWithdrawalsCount[sfrxETHAddress] = unlockedWithdrawalsCountSFRXETH;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L307-307)
```text
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTConfig.sol (L181-184)
```text
    function getContract(bytes32 contractKey) public view override returns (address) {
        UtilLib.checkNonZeroAddress(contractMap[contractKey]);
        return contractMap[contractKey];
    }
```
