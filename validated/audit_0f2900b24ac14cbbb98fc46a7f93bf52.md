### Title
Missing Zero-Address Validation for `admin` and `_rsETHOracle` in Pool Initializers - (File: contracts/pools/RSETHPool.sol)

### Summary
Multiple pool `initialize()` functions accept critical address parameters — specifically `admin` and oracle addresses — without zero-address validation. The most impactful instance is `RSETHPool.sol`, where neither `admin` nor `_rsETHOracle` are checked, while the same pattern recurs across `RSETHPoolV2.sol`, `RSETHPoolV2NBA.sol`, `RSETHPoolV3ExternalBridge.sol`, and `RSETHPoolV3WithNativeChainBridge.sol` (missing check for `admin` and `bridger`).

### Finding Description

In `RSETHPool.sol`'s `initialize()`, only `_rsETH` and `_wstETH` are validated via `UtilLib.checkNonZeroAddress`, while `admin`, `manager`, `_rsETHOracle`, and `_wstETH_ETHOracle` are stored without any zero-address guard:

```solidity
function initialize(
    address admin,
    address manager,
    address _rsETH,
    address _wstETH,
    uint256 _feeBps,
    address _rsETHOracle,
    address _wstETH_ETHOracle
) external initializer {
    UtilLib.checkNonZeroAddress(_rsETH);
    UtilLib.checkNonZeroAddress(_wstETH);
    // admin, manager, _rsETHOracle, _wstETH_ETHOracle — no checks
    ...
    _grantRole(DEFAULT_ADMIN_ROLE, admin);
    _setupRole(LEGACY_MANAGER_ROLE, admin);
    _setupRole(LEGACY_MANAGER_ROLE, manager);
    ...
    rsETHOracle = _rsETHOracle;
    legacyWstETH_ETHOracle = _wstETH_ETHOracle;
}
``` [1](#0-0) 

The same pattern exists in `RSETHPoolV2.sol`, `RSETHPoolV2NBA.sol`, `RSETHPoolV3ExternalBridge.sol`, and `RSETHPoolV3WithNativeChainBridge.sol`, all of which check `_wrsETH` and `_rsETHOracle` but omit checks for `admin` and `bridger`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

By contrast, `UtilLib.checkNonZeroAddress` is available and used consistently elsewhere: [6](#0-5) 

### Impact Explanation

**Scenario A — `admin = address(0)` in `RSETHPool.sol`:**
`DEFAULT_ADMIN_ROLE` is granted to `address(0)`. No real account holds admin rights. Functions gated by `DEFAULT_ADMIN_ROLE` (e.g., `unpause()`) and `TIMELOCK_ROLE` (which requires admin to grant — e.g., `setRSETHOracle`, `setL1VaultETHForL2Chain`, `addSupportedToken`) become permanently inaccessible. If the contract is subsequently paused by a `PAUSER_ROLE` holder, it can never be unpaused, permanently freezing user deposits and pre-loaded rsETH.

**Scenario B — `_rsETHOracle = address(0)` in `RSETHPool.sol`:**
`getRate()` calls `IOracle(address(0)).getRate()`, which reverts. Every call to `deposit()` and `viewSwapRsETHAmountAndFee()` reverts, making the pool non-functional for all depositors until an admin updates the oracle — which is impossible if Scenario A also applies. [7](#0-6) 

### Likelihood Explanation

Likelihood is low but non-negligible. These are upgradeable proxy contracts deployed via scripts. A deployment script error or copy-paste mistake passing `address(0)` for `admin` or `_rsETHOracle` would silently succeed (no revert), leaving the contract in a permanently broken state with no recovery path. The pattern is consistent across five pool contracts, increasing the cumulative probability of a deployment mistake.

### Recommendation

Add `UtilLib.checkNonZeroAddress` guards for all critical address parameters in every pool `initialize()` function. At minimum:

```solidity
// RSETHPool.initialize()
UtilLib.checkNonZeroAddress(admin);
UtilLib.checkNonZeroAddress(manager);
UtilLib.checkNonZeroAddress(_rsETHOracle);

// RSETHPoolV2/V2NBA/V3ExternalBridge/V3WithNativeChainBridge initialize()
UtilLib.checkNonZeroAddress(admin);
UtilLib.checkNonZeroAddress(bridger);
```

### Proof of Concept

1. Deploy `RSETHPool` proxy and call `initialize(address(0), validManager, validRsETH, validWstETH, 0, validOracle, validLegacyOracle)`.
2. Observe: `DEFAULT_ADMIN_ROLE` is held only by `address(0)`.
3. Call `pause()` from a `PAUSER_ROLE` holder.
4. Attempt `unpause()` from any real account → reverts with `AccessControl: account ... is missing role`.
5. All user deposits are permanently frozen with no recovery path.

Alternatively, call `initialize(validAdmin, validManager, validRsETH, validWstETH, 0, address(0), validLegacyOracle)`:
- `rsETHOracle` is set to `address(0)`.
- Any call to `deposit()` → `getRate()` → `IOracle(address(0)).getRate()` → reverts.
- Pool is non-functional for all depositors. [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPool.sol (L222-251)
```text
    function initialize(
        address admin,
        address manager,
        address _rsETH,
        address _wstETH,
        uint256 _feeBps,
        address _rsETHOracle,
        address _wstETH_ETHOracle
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(_rsETH);
        UtilLib.checkNonZeroAddress(_wstETH);

        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        // legacy settings
        _setupRole(LEGACY_MANAGER_ROLE, admin);
        _setupRole(LEGACY_MANAGER_ROLE, manager);

        wrsETH = IERC20Upgradeable(_rsETH);
        legacyWstETH = IERC20Upgradeable(_wstETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
        legacyWstETH_ETHOracle = _wstETH_ETHOracle;
    }
```

**File:** contracts/pools/RSETHPool.sol (L253-256)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L176-198)
```text
    function initialize(
        address admin,
        address bridger,
        address _wrsETH,
        uint256 _feeBps,
        address _rsETHOracle
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        wrsETH = IERC20WrsETH(_wrsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L75-97)
```text
    function initialize(
        address admin,
        address bridger,
        address _wrsETH,
        uint256 _feeBps,
        address _rsETHOracle
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        wrsETH = IERC20WrsETH(_wrsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L330-352)
```text
    function initialize(
        address admin,
        address bridger,
        address _wrsETH,
        uint256 _feeBps,
        address _rsETHOracle
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        wrsETH = IERC20WrsETH(_wrsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L245-268)
```text
    function initialize(
        address admin,
        address bridger,
        address _wrsETH,
        uint256 _feeBps,
        address _rsETHOracle
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        wrsETH = IERC20WrsETH(_wrsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
    }
```

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
