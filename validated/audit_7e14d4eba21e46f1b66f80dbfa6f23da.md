### Title
Missing Zero Address Validation for `admin` in Pool `initialize` Functions - (File: contracts/pools/RSETHPoolV2.sol)

### Summary
Multiple L2 pool `initialize` functions accept an `admin` address parameter without validating it against `address(0)`. If `admin = address(0)` is passed at deployment, `DEFAULT_ADMIN_ROLE` is permanently granted to `address(0)`, making the contract unmanageable with no recovery path.

### Finding Description
`RSETHPoolV2.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPool.sol`, and `RsETHTokenWrapper.sol` all share the same pattern: their `initialize` functions validate token/oracle address parameters via `UtilLib.checkNonZeroAddress` but silently skip the same check for `admin` (and `bridger`).

In `RSETHPoolV2.sol::initialize`:

```solidity
function initialize(
    address admin,
    address bridger,
    address _wrsETH,
    uint256 _feeBps,
    address _rsETHOracle
) external initializer {
    UtilLib.checkNonZeroAddress(_wrsETH);       // ✓ checked
    UtilLib.checkNonZeroAddress(_rsETHOracle);  // ✓ checked
    // admin and bridger are NOT checked ✗
    _grantRole(DEFAULT_ADMIN_ROLE, admin);
    _setupRole(BRIDGER_ROLE, admin);
    _setupRole(BRIDGER_ROLE, bridger);
``` [1](#0-0) 

The same omission appears in `RSETHPoolV2ExternalBridge.sol`: [2](#0-1) 

And in `RSETHPoolV3ExternalBridge.sol`: [3](#0-2) 

And in `RSETHPool.sol` (also skips `_rsETHOracle` and `_wstETH_ETHOracle`): [4](#0-3) 

And in `RsETHTokenWrapper.sol`: [5](#0-4) 

By contrast, `UtilLib.checkNonZeroAddress` is consistently applied to all address parameters in setter functions such as `setRSETHOracle`, `setL1VaultETHForL2Chain`, and `addSupportedToken`: [6](#0-5) 

The utility itself is straightforward: [7](#0-6) 

### Impact Explanation
If `admin = address(0)` is passed to any of these `initialize` calls:

1. `DEFAULT_ADMIN_ROLE` is permanently granted to `address(0)` — no real account holds it.
2. OpenZeppelin `AccessControl` requires `DEFAULT_ADMIN_ROLE` to call `grantRole` / `revokeRole` for any role. With no real holder, no role can ever be granted or revoked.
3. Consequently: no one can ever assign `TIMELOCK_ROLE` (needed to update the oracle or bridge), `PAUSER_ROLE` (needed to pause), or `BRIDGER_ROLE` to new addresses.
4. The pool continues to accept user deposits and mint rsETH, but the oracle can never be updated, fees can never be withdrawn, and the contract can never be paused in an emergency.
5. In `RSETHPool.sol`, `_rsETHOracle` is also unchecked; if it is simultaneously zero, `getRate()` reverts on every call, breaking all deposits immediately.

**Impact: Low** — Contract fails to deliver its promised returns (oracle management, emergency pause, fee collection) but deposited value is not directly stolen.

### Likelihood Explanation
Low. The trigger is a deployment-time mistake: passing `address(0)` as `admin` to `initialize`. No attacker-controlled path is required; the risk is an accidental misconfiguration during proxy setup. The pattern is consistent across five contracts, increasing the cumulative probability of at least one deployment error.

### Recommendation
Add `UtilLib.checkNonZeroAddress(admin)` (and `UtilLib.checkNonZeroAddress(bridger)` where applicable) at the top of each `initialize` function, consistent with how `_wrsETH` and `_rsETHOracle` are already validated. For `RSETHPool.sol`, also add checks for `_rsETHOracle` and `_wstETH_ETHOracle`.

### Proof of Concept
1. Deploy `RSETHPoolV2` behind a proxy.
2. Call `initialize(address(0), validBridger, validWrsETH, feeBps, validOracle)`.
3. `DEFAULT_ADMIN_ROLE` is silently granted to `address(0)`.
4. Attempt `grantRole(TIMELOCK_ROLE, timelockAddr)` — reverts: caller lacks `DEFAULT_ADMIN_ROLE`.
5. Oracle becomes stale; no one can call `setRSETHOracle` (requires `TIMELOCK_ROLE`).
6. A critical bug is discovered; no one can call `pause` (requires a role that can never be granted).
7. The pool is permanently unmanageable; its promised operational guarantees are broken for all depositors.

### Citations

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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L258-280)
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

**File:** contracts/pools/RSETHPool.sol (L605-611)
```text
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        rsETHOracle = _rsETHOracle;

        emit OracleSet(_rsETHOracle);
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

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
