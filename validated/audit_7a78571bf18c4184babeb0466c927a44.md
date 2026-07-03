### Title
Missing Input Validation in `initialize` Allows Contract Deployment in Inconsistent State - (File: contracts/agETH/AGETHTokenWrapper.sol)

### Summary
`AGETHTokenWrapper.initialize` accepts `admin`, `manager`, and `_altAgETH` without any zero-address validation. A deployment with any of these as `address(0)` permanently misconfigures the wrapper, mirroring the exact vulnerability class described in the external report.

### Finding Description
The `initialize` function in `AGETHTokenWrapper` performs no input validation before assigning roles and registering the allowed token:

```solidity
function initialize(address admin, address manager, address _altAgETH) external initializer {
    __ERC20_init("agETHWrapper", "agETH");
    __ERC20Permit_init("agETHWrapper");
    __AccessControl_init();

    _setupRole(DEFAULT_ADMIN_ROLE, admin);   // no zero-address check
    _setupRole(MANAGER_ROLE, manager);       // no zero-address check
    _setupRole(BRIDGER_ROLE, manager);       // no zero-address check

    allowedTokens[_altAgETH] = true;         // no zero-address check
}
``` [1](#0-0) 

Contrast this with the sibling contract `RsETHTokenWrapper`, which also lacks zero-address checks: [2](#0-1) 

And compare with contracts that do apply the `UtilLib.checkNonZeroAddress` guard correctly, such as `KernelReceiver`: [3](#0-2) 

The utility function used elsewhere in the codebase is: [4](#0-3) 

Three distinct misconfiguration paths exist:

1. **`admin = address(0)`**: `DEFAULT_ADMIN_ROLE` is granted to `address(0)`. No human account holds this role. No one can subsequently grant `MINTER_ROLE` to bridge/pool contracts, add or remove allowed tokens, or perform any admin action. The contract is permanently unmanageable.

2. **`manager = address(0)`**: `MANAGER_ROLE` and `BRIDGER_ROLE` are granted to `address(0)`. No human holds these roles, so bridging and management operations are permanently inaccessible.

3. **`_altAgETH = address(0)`**: `allowedTokens[address(0)] = true`. The zero address is registered as a valid deposit/withdrawal asset, creating an inconsistent state. Any call to `deposit(address(0), amount)` or `withdraw(address(0), amount)` will pass the `allowedTokens` guard but revert at the ERC-20 transfer step, silently breaking the deposit/withdrawal path for that token slot.

### Impact Explanation
**Low — Contract fails to deliver promised returns, but does not lose value.**

If `admin` is `address(0)`, the wrapper is permanently unmanageable: no roles can be granted, no new allowed tokens can be added, and no upgrades can be authorized. Users who have already deposited altAgETH and hold agETH wrapper tokens can still call `withdraw` (no role required), so existing funds are not frozen. However, the contract can never be brought into a correct operational state without redeployment, and any functionality gated on `MANAGER_ROLE` or `BRIDGER_ROLE` is permanently unavailable.

### Likelihood Explanation
Deployment mistakes of this kind are the exact scenario the external report targets. The `initialize` function is called exactly once, is irreversible, and has no on-chain guard to catch a zero-address argument. The absence of any check makes a misconfiguration silently succeed rather than revert, making the error hard to detect until the contract is already live.

### Recommendation
Add `UtilLib.checkNonZeroAddress` guards for all address parameters before any state is written, consistent with the pattern used in `KernelReceiver`, `KernelDepositPool`, and other contracts in the same codebase:

```solidity
function initialize(address admin, address manager, address _altAgETH) external initializer {
+   UtilLib.checkNonZeroAddress(admin);
+   UtilLib.checkNonZeroAddress(manager);
+   UtilLib.checkNonZeroAddress(_altAgETH);
    __ERC20_init("agETHWrapper", "agETH");
    __ERC20Permit_init("agETHWrapper");
    __AccessControl_init();
    _setupRole(DEFAULT_ADMIN_ROLE, admin);
    _setupRole(MANAGER_ROLE, manager);
    _setupRole(BRIDGER_ROLE, manager);
    allowedTokens[_altAgETH] = true;
}
```

Apply the same fix to `RsETHTokenWrapper.initialize` for `admin`, `bridger`, and `_altRsETH`. [2](#0-1) 

### Proof of Concept
1. Deploy `AGETHTokenWrapper` behind a proxy.
2. Call `initialize(address(0), validManager, validAltAgETH)`.
3. The call succeeds; `DEFAULT_ADMIN_ROLE` is held only by `address(0)`.
4. Attempt to call any admin-gated function (e.g., grant `MINTER_ROLE` to a bridge contract) — it reverts with `AccessControl: account 0x... is missing role`.
5. The contract is permanently unmanageable; redeployment is the only remedy.

### Citations

**File:** contracts/agETH/AGETHTokenWrapper.sol (L45-55)
```text
    function initialize(address admin, address manager, address _altAgETH) external initializer {
        __ERC20_init("agETHWrapper", "agETH");
        __ERC20Permit_init("agETHWrapper");
        __AccessControl_init();

        _setupRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(MANAGER_ROLE, manager);
        _setupRole(BRIDGER_ROLE, manager);

        allowedTokens[_altAgETH] = true;
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L55-64)
```text
    function initialize(address admin, address bridger, address _altRsETH) external initializer {
        __ERC20_init("rsETHWrapper", "wrsETH");
        __ERC20Permit_init("rsETHWrapper");
        __AccessControl_init();

        _setupRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/KERNEL/KernelReceiver.sol (L101-104)
```text
        UtilLib.checkNonZeroAddress(_admin);
        UtilLib.checkNonZeroAddress(_operator);
        UtilLib.checkNonZeroAddress(_kernel);
        UtilLib.checkNonZeroAddress(_stakerGateway);
```

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
