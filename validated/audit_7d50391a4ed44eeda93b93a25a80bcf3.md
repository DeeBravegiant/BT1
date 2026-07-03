### Title
`LRTUnstakingVault` Inherits `PausableUpgradeable` But Exposes No `pause()` Function and Applies No `whenNotPaused` Modifier to Any State-Changing Function - (File: contracts/LRTUnstakingVault.sol)

### Summary
`LRTUnstakingVault` inherits from `PausableUpgradeable` and calls `__Pausable_init()` in its initializer, but never exposes a `pause()` function and never applies the `whenNotPaused` modifier to any of its state-changing functions. The pause mechanism is therefore completely non-functional for this contract: it cannot be paused, and even if it were somehow paused, no function would respect the paused state. Additionally, `LRTConfig.pauseAll()` does not include `LRTUnstakingVault` in its emergency pause sweep.

### Finding Description
`LRTUnstakingVault` declares inheritance from `PausableUpgradeable` and initializes it: [1](#0-0) [2](#0-1) 

However, none of its state-changing functions carry `whenNotPaused`:

- `redeem()` — transfers ETH or ERC20 assets to the withdrawal manager [3](#0-2) 
- `transferAssetToNodeDelegator()` — moves LST assets from the vault to a node delegator [4](#0-3) 
- `transferETHToNodeDelegator()` — moves ETH from the vault to a node delegator [5](#0-4) 
- `increaseUncompletedWithdrawalCount()` / `decreaseUncompletedWithdrawalCount()` — accounting state mutations [6](#0-5) 

No `pause()` or `unpause()` function is defined anywhere in the contract. Compare this with `LRTDepositPool`, `LRTWithdrawalManager`, `NodeDelegator`, and `RSETH`, all of which expose `pause()` guarded by `PAUSER_ROLE` and apply `whenNotPaused` to their critical paths.

Furthermore, `LRTConfig.pauseAll()` — the protocol's emergency stop — explicitly pauses `lrtDepositPool`, `lrtWithdrawalManager`, `lrtOracle`, `rsETHContract`, and all node delegators, but never touches `LRTUnstakingVault`: [7](#0-6) 

### Impact Explanation
`LRTUnstakingVault` is the custodian of user assets that are in-flight through the withdrawal queue — ETH and LSTs awaiting delivery to withdrawing users. In an emergency requiring a full protocol halt (e.g., a discovered exploit in the withdrawal or unstaking path), the protocol has no mechanism to stop fund movements through this contract. Operators retaining the `ASSET_TRANSFER_ROLE` can still call `transferAssetToNodeDelegator` and `transferETHToNodeDelegator` to move vault funds into node delegators, and the withdrawal manager (if not itself paused) can still call `redeem()`. The promised invariant — that pausing the protocol stops all material fund flows — is broken for this contract.

**Impact: Low** — the contract fails to deliver the promised pause guarantee. No direct value loss occurs absent a concurrent exploit, but the safety net is absent precisely when it is needed most.

### Likelihood Explanation
The missing `pause()` function and absent `whenNotPaused` modifiers are unconditional code-level omissions, present in every deployment. The gap is triggered whenever an operator invokes a transfer function during a period when the protocol is supposed to be halted. Likelihood is low in normal operation but becomes relevant exactly during the emergency scenarios the pause mechanism is designed to address.

### Recommendation
1. Add a `pause()` function guarded by `PAUSER_ROLE` and an `unpause()` function guarded by `onlyLRTAdmin` to `LRTUnstakingVault`, mirroring the pattern in `LRTDepositPool` and `NodeDelegator`.
2. Add `whenNotPaused` to `redeem()`, `transferAssetToNodeDelegator()`, and `transferETHToNodeDelegator()`.
3. Include `LRTUnstakingVault` in the `LRTConfig.pauseAll()` sweep alongside the other core contracts.

### Proof of Concept
1. Deploy the protocol. Call `LRTConfig.pauseAll()` to simulate an emergency halt.
2. Observe that `LRTUnstakingVault` is not paused (no `pause()` call is made to it in `pauseAll()`).
3. Call `LRTUnstakingVault.transferAssetToNodeDelegator(ndcIndex, asset, amount)` from an `ASSET_TRANSFER_ROLE` account — the call succeeds, moving user funds out of the vault despite the protocol-wide emergency pause.
4. Confirm that the `paused` state variable inherited from `PausableUpgradeable` is `false` and that no function in the contract checks it. [4](#0-3) [7](#0-6)

### Citations

**File:** contracts/LRTUnstakingVault.sol (L25-29)
```text
contract LRTUnstakingVault is
    ILRTUnstakingVault,
    LRTConfigRoleChecker,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
```

**File:** contracts/LRTUnstakingVault.sol (L68-71)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
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

**File:** contracts/LRTUnstakingVault.sol (L112-126)
```text
    function transferAssetToNodeDelegator(
        uint256 ndcIndex,
        address asset,
        uint256 amount
    )
        external
        nonReentrant
        onlyAssetTransferRole
        onlySupportedAsset(asset)
    {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.depositPool());
        address[] memory nodeDelegatorQueue = lrtDepositPool.getNodeDelegatorQueue();
        address nodeDelegator = nodeDelegatorQueue[ndcIndex];
        IERC20(asset).safeTransfer(nodeDelegator, amount);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L132-146)
```text
    function transferETHToNodeDelegator(
        uint256 ndcIndex,
        uint256 amount
    )
        external
        nonReentrant
        onlyAssetTransferRole
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.depositPool());
        address[] memory nodeDelegatorQueue = lrtDepositPool.getNodeDelegatorQueue();
        address nodeDelegator = nodeDelegatorQueue[ndcIndex];
        INodeDelegator(nodeDelegator).sendETHFromUnstakingVaultToNDC{ value: amount }();
        emit EthTransferred(nodeDelegator, amount);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L184-194)
```text
    function increaseUncompletedWithdrawalCount() external onlyLRTNodeDelegator {
        uncompletedWithdrawalCount++;
    }

    /// @notice Decrease the number of uncompleted withdrawals.
    /// @dev This function is only callable by the NodeDelegator contracts during the unstaking process.
    function decreaseUncompletedWithdrawalCount() external onlyLRTNodeDelegator {
        if (uncompletedWithdrawalCount > 0) {
            uncompletedWithdrawalCount--;
        }
    }
```

**File:** contracts/LRTConfig.sol (L262-285)
```text
    function pauseAll() external onlyRole(LRTConstants.PAUSER_ROLE) {
        IPausable lrtDepositPool = IPausable(getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable lrtWithdrawalManager = IPausable(getContract(LRTConstants.LRT_WITHDRAW_MANAGER));
        IPausable lrtOracle = IPausable(getContract(LRTConstants.LRT_ORACLE));
        IPausable rsETHContract = IPausable(rsETH);

        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!lrtWithdrawalManager.paused()) lrtWithdrawalManager.pause();
        if (!lrtOracle.paused()) lrtOracle.pause();
        if (!rsETHContract.paused()) rsETHContract.pause();

        address[] memory nodeDelegatorQueue = ILRTDepositPool(address(lrtDepositPool)).getNodeDelegatorQueue();
        uint256 nodeDelegatorCount = nodeDelegatorQueue.length;

        for (uint256 i = 0; i < nodeDelegatorCount;) {
            IPausable nodeDelegator = IPausable(nodeDelegatorQueue[i]);
            if (!nodeDelegator.paused()) nodeDelegator.pause();
            unchecked {
                ++i;
            }
        }

        emit PausedAll(msg.sender);
    }
```
