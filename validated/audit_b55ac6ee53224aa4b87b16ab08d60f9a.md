### Title
`TokenSwap.pause()` Callable by Manager Instead of Admin, Enabling Temporary Fund Freeze - (File: contracts/king-protocol/TokenSwap.sol)

### Summary
In `TokenSwap.sol`, the `pause()` function is guarded by `onlyManager` despite its NatSpec explicitly stating "admin only." The `unpause()` function correctly uses `onlyAdmin`. This privilege asymmetry mirrors the M-14 pattern exactly: a lower-privileged role (manager) can trigger a critical protocol-halting action that should require the highest-privilege role (admin).

### Finding Description
`TokenSwap.sol` defines two privilege levels: `onlyAdmin` (checks `DEFAULT_ADMIN_ROLE`) and `onlyManager` (checks `MANAGER_ROLE`). Admin is the higher-privilege role — it controls `unpause()`, `emergencyWithdraw()`, and role grants. Manager is a lower-privilege operational role.

The `pause()` function at line 398 uses `onlyManager`, but its NatSpec comment at line 397 reads `"Pause the contract (admin only)"`. The `unpause()` function at line 404 correctly uses `onlyAdmin`. This creates a one-way asymmetry: a manager can freeze the contract but cannot unfreeze it — only admin can.

The three operational functions gated by `whenNotPaused` are:
- `depositToKingProtocol()` (line 151)
- `depositMultipleToKingProtocol()` (line 201)
- `withdrawKing()` (line 282)

All three are blocked when the contract is paused, freezing all supported tokens and KING tokens held in the contract.

### Impact Explanation
A manager can call `pause()` at any time, immediately halting all deposits to King Protocol and all KING token withdrawals. All assets held in the `TokenSwap` contract are frozen until admin explicitly calls `unpause()`. If admin is a multisig or timelock, recovery may be delayed. This constitutes **temporary freezing of funds** (Medium).

### Likelihood Explanation
The `MANAGER_ROLE` is a separate, lower-privilege operational role granted at initialization. Any holder of this role — whether acting maliciously or under compromise — can trigger the freeze with a single transaction. The NatSpec mismatch ("admin only" comment vs. `onlyManager` implementation) confirms this is an unintentional design error, not a deliberate emergency-response pattern.

### Recommendation
Change the `pause()` modifier from `onlyManager` to `onlyAdmin` to match the stated intent in the NatSpec and to align with the privilege level of `unpause()`. If a lower-privilege emergency pause is genuinely desired, introduce a dedicated `PAUSER_ROLE` (as already defined in `LRTConstants.sol` line 34) and document the asymmetry explicitly.

### Proof of Concept
1. Manager (holder of `MANAGER_ROLE`) calls `TokenSwap.pause()`.
2. `_pause()` executes; contract enters paused state.
3. Any subsequent call to `depositToKingProtocol()`, `depositMultipleToKingProtocol()`, or `withdrawKing()` reverts due to `whenNotPaused`.
4. All supported tokens and KING tokens held in the contract are frozen.
5. Only admin can call `unpause()` to restore functionality — manager cannot.

**Relevant lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/king-protocol/TokenSwap.sol (L78-97)
```text
    modifier onlyAdmin() {
        if (!hasRole(LRTConstants.DEFAULT_ADMIN_ROLE, msg.sender)) {
            revert("Caller is not admin");
        }
        _;
    }

    modifier onlyManager() {
        if (!hasRole(MANAGER_ROLE, msg.sender)) {
            revert("Caller is not manager");
        }
        _;
    }

    modifier onlyAdminOrManager() {
        if (!hasRole(LRTConstants.DEFAULT_ADMIN_ROLE, msg.sender) && !hasRole(MANAGER_ROLE, msg.sender)) {
            revert("Caller is not admin or manager");
        }
        _;
    }
```

**File:** contracts/king-protocol/TokenSwap.sol (L145-153)
```text
    function depositToKingProtocol(
        address asset,
        uint256 amount
    )
        external
        nonReentrant
        whenNotPaused
        onlyAdminOrManager
        returns (uint256 shareReceived)
```

**File:** contracts/king-protocol/TokenSwap.sol (L282-283)
```text
    function withdrawKing(address recipient, uint256 amount) external nonReentrant whenNotPaused onlyAdminOrManager {
        if (amount == 0) {
```

**File:** contracts/king-protocol/TokenSwap.sol (L397-407)
```text
    /// @notice Pause the contract (admin only)
    function pause() external onlyManager {
        _pause();
        emit PauseStateChanged(true);
    }

    /// @notice Unpause the contract (admin only)
    function unpause() external onlyAdmin {
        _unpause();
        emit PauseStateChanged(false);
    }
```
