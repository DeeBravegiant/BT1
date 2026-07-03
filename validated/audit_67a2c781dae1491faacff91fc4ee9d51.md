### Title
`pause()` Callable by Lower-Privileged `MANAGER_ROLE` Instead of `DEFAULT_ADMIN_ROLE`, Enabling Malicious Manager to Temporarily Freeze Protocol Operations - (File: contracts/king-protocol/TokenSwap.sol)

### Summary
In `TokenSwap.sol`, the `pause()` function is gated by `onlyManager` while `unpause()` requires `onlyAdmin`. A malicious or compromised manager can repeatedly pause the contract, blocking all `whenNotPaused` operations, while the admin is unable to prevent re-pausing short of revoking the manager's role entirely.

### Finding Description
The `TokenSwap` contract defines two distinct privilege levels: `DEFAULT_ADMIN_ROLE` (admin) and `MANAGER_ROLE` (manager). The `pause()` function is annotated with the NatSpec comment `"Pause the contract (admin only)"` but is actually guarded by `onlyManager`, a lower-privileged role. The `unpause()` function is correctly guarded by `onlyAdmin`. [1](#0-0) 

The `onlyManager` modifier checks only for `MANAGER_ROLE`, which is a separate, lower-privileged role from `DEFAULT_ADMIN_ROLE`: [2](#0-1) 

Three core operational functions are gated by `whenNotPaused`:
- `depositToKingProtocol()` [3](#0-2) 
- `depositMultipleToKingProtocol()` [4](#0-3) 
- `withdrawKing()` [5](#0-4) 

When the contract is paused, all three of these functions revert, freezing normal token flow through the contract.

### Impact Explanation
A malicious manager can call `pause()` at any time to block all deposits to King Protocol and all KING token withdrawals. Even if the admin calls `unpause()`, the manager can immediately re-pause in the same block or the next, creating a sustained denial-of-service. The only remediation available to the admin is to revoke the manager's role via `revokeRole`, which is an out-of-band recovery action. The `emergencyWithdraw()` function is not paused, so funds are not permanently lost, but normal protocol operations are temporarily frozen. This maps to **Medium — Temporary freezing of funds**. [6](#0-5) 

### Likelihood Explanation
The `MANAGER_ROLE` is an operational role granted at initialization to a separate address from the admin. It is a realistic attack surface: a compromised manager key, a rogue operator, or a social-engineering attack on the manager address is sufficient to trigger this. No user funds need to be at risk for the attacker to cause significant operational disruption. Likelihood is **Medium**.

### Recommendation
Change the access control on `pause()` from `onlyManager` to `onlyAdmin`, consistent with the NatSpec comment already present and consistent with the pattern used by `unpause()`. Alternatively, introduce a dedicated `PAUSER_ROLE` (already defined in `LRTConstants`) and grant it only to the admin or a trusted multisig, mirroring the pattern used in the rest of the LRT-rsETH protocol.

```solidity
// Before (vulnerable)
function pause() external onlyManager { ... }

// After (fixed)
function pause() external onlyAdmin { ... }
```

### Proof of Concept
1. Admin deploys `TokenSwap` and grants `MANAGER_ROLE` to `manager`.
2. `manager` calls `pause()` — succeeds because `onlyManager` is satisfied.
3. Admin calls `unpause()` — succeeds.
4. `manager` immediately calls `pause()` again — succeeds again.
5. All calls to `depositToKingProtocol`, `depositMultipleToKingProtocol`, and `withdrawKing` revert with `Pausable: paused`.
6. The loop continues until admin calls `revokeRole(MANAGER_ROLE, manager)`.

Relevant lines:
- `pause()` with incorrect `onlyManager` guard: [7](#0-6) 
- `unpause()` with correct `onlyAdmin` guard: [8](#0-7) 
- Role definitions showing `MANAGER_ROLE` is separate from and lower than `DEFAULT_ADMIN_ROLE`: [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/king-protocol/TokenSwap.sol (L20-20)
```text
    bytes32 public constant MANAGER_ROLE = keccak256("MANAGER_ROLE");
```

**File:** contracts/king-protocol/TokenSwap.sol (L85-90)
```text
    modifier onlyManager() {
        if (!hasRole(MANAGER_ROLE, msg.sender)) {
            revert("Caller is not manager");
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

**File:** contracts/king-protocol/TokenSwap.sol (L196-204)
```text
    function depositMultipleToKingProtocol(
        address[] memory assets,
        uint256[] memory amounts
    )
        external
        nonReentrant
        whenNotPaused
        onlyAdminOrManager
        returns (uint256 shareReceived)
```

**File:** contracts/king-protocol/TokenSwap.sol (L282-282)
```text
    function withdrawKing(address recipient, uint256 amount) external nonReentrant whenNotPaused onlyAdminOrManager {
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

**File:** contracts/king-protocol/TokenSwap.sol (L483-496)
```text
    /// @notice Emergency function to withdraw any ERC20 token (admin only)
    /// @param token The token to withdraw
    /// @param recipient The recipient address
    /// @param amount The amount to withdraw
    function emergencyWithdraw(address token, address recipient, uint256 amount) external onlyAdmin {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(recipient);

        if (amount == 0) {
            revert ZeroAmount();
        }

        IERC20(token).safeTransfer(recipient, amount);
    }
```

**File:** contracts/utils/LRTConstants.sol (L32-33)
```text
    bytes32 public constant DEFAULT_ADMIN_ROLE = 0x00;
    bytes32 public constant MANAGER = keccak256("MANAGER");
```
