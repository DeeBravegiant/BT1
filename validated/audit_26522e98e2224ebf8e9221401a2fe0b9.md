### Title
Missing Zero Address Validation for `admin` Before Granting `DEFAULT_ADMIN_ROLE` in Pool Initializers - (File: contracts/pools/RSETHPoolV2.sol, contracts/pools/RSETHPoolV2NBA.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/agETH/AGETHPoolV3.sol, contracts/agETH/AGETHTokenWrapper.sol)

---

### Summary

Multiple pool and wrapper contracts grant `DEFAULT_ADMIN_ROLE` (and `BRIDGER_ROLE`) to caller-supplied `admin` and `bridger` addresses during `initialize()` without first validating that those addresses are non-zero. If either address is `address(0)`, the role is permanently assigned to the zero address, making all role-gated administrative functions permanently inaccessible and locking any accumulated fees or bridgeable assets inside the contract forever.

---

### Finding Description

`UtilLib.checkNonZeroAddress` is the project-standard guard used throughout the codebase to reject zero addresses. In every affected `initialize()` function the guard is applied to contract-address parameters (`_wrsETH`, `_rsETHOracle`, `_agETH`, etc.) but is **omitted** for the `admin` and `bridger` role-recipient parameters.

Affected initializers and the missing checks:

**`RSETHPoolV2.sol`**
```solidity
function initialize(address admin, address bridger, address _wrsETH, ...) external initializer {
    UtilLib.checkNonZeroAddress(_wrsETH);      // ✓ checked
    UtilLib.checkNonZeroAddress(_rsETHOracle); // ✓ checked
    // ✗ admin and bridger are NOT checked
    _grantRole(DEFAULT_ADMIN_ROLE, admin);
    _setupRole(BRIDGER_ROLE, admin);
    _setupRole(BRIDGER_ROLE, bridger);
    ...
}
``` [1](#0-0) 

The identical pattern appears in:
- `RSETHPoolV2NBA.sol` [2](#0-1) 
- `RSETHPoolV3.sol` [3](#0-2) 
- `RSETHPoolV3ExternalBridge.sol` [4](#0-3) 
- `RSETHPoolV3WithNativeChainBridge.sol` [5](#0-4) 
- `AGETHPoolV3.sol` [6](#0-5) 
- `AGETHTokenWrapper.sol` — **no zero-address checks at all** for `admin`, `manager`, or `_altAgETH` [7](#0-6) 

`UtilLib.checkNonZeroAddress` reverts with `ZeroAddressNotAllowed` when the address is zero; its absence here means the zero address silently receives the role. [8](#0-7) 

---

### Impact Explanation

If `admin == address(0)` is supplied at initialization:

1. `DEFAULT_ADMIN_ROLE` is permanently held by `address(0)`.
2. No live account can ever call functions gated by `onlyRole(DEFAULT_ADMIN_ROLE)` — including `setFeeBps`, `unpause`, `setDailyMintLimit`, and role-granting itself.
3. Because `DEFAULT_ADMIN_ROLE` is the admin of every other role, no new `BRIDGER_ROLE` holder can ever be granted; `withdrawFees` and `moveAssetsForBridging` become permanently inaccessible.
4. All ETH and ERC-20 tokens accumulated as fees or pending bridging are **permanently frozen** inside the contract.

If `bridger == address(0)` is supplied alongside a valid `admin`, the `BRIDGER_ROLE` is also silently granted to zero address (a no-op holder), but the admin can still grant the role to a real address — so the impact is lower in that sub-case.

**Impact: Critical — Permanent freezing of funds** (accumulated fees and bridgeable assets locked forever with no recovery path).

---

### Likelihood Explanation

The `initialize()` function is `external` and protected only by the `initializer` modifier, meaning it can be called by **any account** before the proxy is initialized. A deployer error (passing `address(0)`) or a front-run of the initialization transaction by an attacker who supplies `address(0)` as `admin` would trigger the condition. Given that these are upgradeable proxy contracts deployed across multiple L2 chains, the deployment surface is wide. Likelihood is **Low** (requires either a deployment mistake or a narrow front-run window), but the consequence is irreversible.

---

### Recommendation

Add `UtilLib.checkNonZeroAddress` guards for every role-recipient address before any `_grantRole` / `_setupRole` call in each affected initializer, mirroring the pattern already used for contract-address parameters:

```solidity
UtilLib.checkNonZeroAddress(admin);
UtilLib.checkNonZeroAddress(bridger);
_grantRole(DEFAULT_ADMIN_ROLE, admin);
_setupRole(BRIDGER_ROLE, bridger);
```

---

### Proof of Concept

1. Deploy the `RSETHPoolV2` proxy (or any affected pool proxy).
2. Before the legitimate deployer calls `initialize`, call:
   ```solidity
   pool.initialize(
       address(0),   // admin = zero address
       address(0),   // bridger = zero address
       wrsETH,
       feeBps,
       rsETHOracle
   );
   ```
3. Initialization succeeds; `DEFAULT_ADMIN_ROLE` and `BRIDGER_ROLE` are both held by `address(0)`.
4. Users deposit ETH; fees accumulate in `feeEarnedInETH`.
5. Any call to `withdrawFees`, `moveAssetsForBridging`, `setFeeBps`, or `unpause` reverts with `AccessControl: account 0x000...000 is missing role ...`.
6. All accumulated ETH is permanently locked — no admin exists to recover it or grant the role to a new address.

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L186-193)
```text
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L85-92)
```text
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);
```

**File:** contracts/pools/RSETHPoolV3.sol (L218-226)
```text
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L340-347)
```text
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L255-263)
```text
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L86-95)
```text
        UtilLib.checkNonZeroAddress(_agETH);
        UtilLib.checkNonZeroAddress(_agETHOracle);

        __ERC20_init("agETH", "agETH");
        __AccessControl_init();
        __ReentrancyGuard_init();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);
```

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

**File:** contracts/utils/UtilLib.sol (L10-13)
```text
    /// @param address_ address to check
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
