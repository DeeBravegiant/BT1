### Title
Incomplete Pause Implementation in `LRTUnstakingVault` — (`File: contracts/LRTUnstakingVault.sol`)

### Summary
`LRTUnstakingVault` imports and initializes `PausableUpgradeable` but exposes no `pause()`, no `unpause()`, and applies `whenNotPaused` to none of its functions. The pause infrastructure is entirely dead code, leaving the vault — which holds user ETH and LSTs being unstaked from EigenLayer — permanently un-pausable by the protocol.

### Finding Description
`LRTUnstakingVault` inherits from `PausableUpgradeable` and calls `__Pausable_init()` during initialization: [1](#0-0) [2](#0-1) [3](#0-2) 

Yet the contract defines no `pause()` function, no `unpause()` function, and no `whenNotPaused` modifier on any of its state-changing functions:

- `redeem()` — pulls ETH/LST from the vault to the withdrawal manager for user payouts. [4](#0-3) 
- `transferAssetToNodeDelegator()` — moves LSTs from the vault back to a node delegator. [5](#0-4) 
- `transferETHToNodeDelegator()` — moves ETH from the vault back to a node delegator. [6](#0-5) 

Every other core contract in the protocol correctly exposes `pause()`/`unpause()` and guards critical paths with `whenNotPaused`:

- `LRTDepositPool` — `pause()` at line 349, `whenNotPaused` on `depositETH` and `depositAsset`. [7](#0-6) 
- `LRTWithdrawalManager` — `pause()` at line 347, `whenNotPaused` on `initiateWithdrawal`, `completeWithdrawal`, `instantWithdrawal`, `unlockQueue`. [8](#0-7) 
- `NodeDelegator` — `pause()` at line 535, `whenNotPaused` on `depositAssetIntoStrategy`, `stake32Eth`, `completeUnstaking`, etc. [9](#0-8) 

`LRTUnstakingVault` is the only fund-holding contract in the protocol that is structurally un-pausable.

### Impact Explanation
The vault holds the ETH and LSTs that have been unstaked from EigenLayer and are awaiting distribution to withdrawing users. In an emergency (e.g., a bug discovered in the vault's accounting or in the withdrawal manager's interaction with it), the protocol can pause `LRTDepositPool` and `LRTWithdrawalManager` to halt new deposits and queued withdrawals, but it cannot pause `LRTUnstakingVault`. The `redeem()` and `transferAsset*` paths remain live. The protocol's emergency response is structurally incomplete.

**Impact: Low — contract fails to deliver the promised pause capability without direct loss of value.**

### Likelihood Explanation
The `PausableUpgradeable` import and `__Pausable_init()` call are present, indicating the pause mechanism was intended. Every other fund-holding contract in the system is pausable. The omission is likely an oversight during development. Likelihood of this gap mattering in a real emergency is moderate given the vault's central role in the unstaking flow.

### Recommendation
Add `pause()` and `unpause()` functions with appropriate role-based access control (e.g., `PAUSER_ROLE` / `onlyLRTAdmin` consistent with the rest of the protocol), and apply `whenNotPaused` to `redeem()`, `transferAssetToNodeDelegator()`, and `transferETHToNodeDelegator()`.

### Proof of Concept
1. `LRTUnstakingVault` inherits `PausableUpgradeable` and calls `__Pausable_init()`. [3](#0-2) 
2. A search of the entire file reveals zero occurrences of `pause`, `unpause`, or `whenNotPaused` beyond the initializer call — the inherited state variable `_paused` is set to `false` and never changed. [10](#0-9) 
3. In an emergency, the protocol pauses `LRTDepositPool` and `LRTWithdrawalManager`, but `LRTUnstakingVault.redeem()` (callable by the withdrawal manager) and `transferAssetToNodeDelegator()` / `transferETHToNodeDelegator()` (callable by the asset transfer role) remain fully operational with no on-chain mechanism to stop them.

### Citations

**File:** contracts/LRTUnstakingVault.sol (L16-16)
```text
import { PausableUpgradeable } from "@openzeppelin/contracts-upgradeable/security/PausableUpgradeable.sol";
```

**File:** contracts/LRTUnstakingVault.sol (L25-30)
```text
contract LRTUnstakingVault is
    ILRTUnstakingVault,
    LRTConfigRoleChecker,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
```

**File:** contracts/LRTUnstakingVault.sol (L68-75)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();

        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L99-146)
```text
    function redeem(address asset, uint256 amount) external nonReentrant onlyLRTWithdrawalManager {
        if (asset == LRTConstants.ETH_TOKEN) {
            ILRTWithdrawalManager(msg.sender).receiveFromLRTUnstakingVault{ value: amount }();
        } else {
            IERC20(asset).safeTransfer(msg.sender, amount);
        }
    }

    /// @notice transfers asset lying in this LRTUnstakingVault to node delegator contract
    /// @dev only callable by Asset Transfer Role
    /// @param ndcIndex Index of NodeDelegator contract address in nodeDelegatorQueue
    /// @param asset Asset address
    /// @param amount Asset amount to transfer
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

    /// @notice transfers ETH lying in this LRTUnstakingVault to node delegator contract
    /// @dev only callable by Asset Transfer Role
    /// @param ndcIndex Index of NodeDelegator contract address in nodeDelegatorQueue
    /// @param amount ETH amount to transfer
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

**File:** contracts/LRTDepositPool.sol (L349-356)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }

    /// @dev Returns to normal state. Contract must be paused.
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L347-354)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }

    /// @dev Returns to normal state. Contract must be paused.
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```

**File:** contracts/NodeDelegator.sol (L535-542)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }

    /// @dev Returns to normal state. Contract must be paused.
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```
