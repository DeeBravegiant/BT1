Audit Report

## Title
Uninitialized `dailyMintLimit` Causes `limitDailyMint` Modifier to Always Revert, Blocking All Deposits on Fresh Deployments - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
`RSETHPoolV3.initialize()` and `RSETHPoolV3ExternalBridge.initialize()` both leave `dailyMintLimit` at its Solidity default of `0`. The `limitDailyMint` modifier, applied to every `deposit()` entry point in both contracts, unconditionally reverts with `DailyMintLimitExceeded` for any non-zero deposit when `dailyMintLimit == 0`. Any freshly deployed proxy that has not yet had `setDailyMintLimit()` (or the appropriate `reinitialize`) called is entirely unable to accept deposits.

## Finding Description
`RSETHPoolV3.initialize()` sets `wrsETH`, `feeBps`, `rsETHOracle`, and `isEthDepositEnabled`, but never assigns `dailyMintLimit` or `startTimestamp`. [1](#0-0) 

`dailyMintLimit` is only written by `reinitialize(uint256,uint256)` (tagged `reinitializer(2)`) and by `setDailyMintLimit()`. Neither is called atomically with `initialize()`. [2](#0-1) [3](#0-2) 

The `limitDailyMint` modifier first checks `block.timestamp < startTimestamp`. With `startTimestamp == 0`, this evaluates to `block.timestamp < 0`, which is always `false` for a `uint256` (never reverts). Execution then reaches the cap check:

```solidity
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
``` [4](#0-3) 

With `dailyMintLimit == 0`, this reduces to `rsETHAmount > 0`, which is always true for any real deposit, causing an unconditional revert. Both `deposit(string)` and `deposit(address,uint256,string)` carry this modifier: [5](#0-4) [6](#0-5) 

The identical pattern exists in `RSETHPoolV3ExternalBridge.sol`: `initialize()` does not set `dailyMintLimit`, [7](#0-6) 

and the same modifier check unconditionally reverts: [8](#0-7) 

## Impact Explanation
Every user-facing deposit path on the L2 pool is gated by `limitDailyMint`. With `dailyMintLimit == 0`, no depositor can mint wrsETH regardless of asset or amount. The contract entirely fails to deliver its core function — accepting deposits and minting wrsETH — until an admin separately calls `setDailyMintLimit()`. No user funds are lost (ETH reverts with the transaction), but the contract delivers no promised returns.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
Any new proxy deployment of `RSETHPoolV3` or `RSETHPoolV3ExternalBridge` (e.g., onboarding a new L2 chain) that calls only `initialize()` and omits the separate `setDailyMintLimit()` step will be silently broken from day one. The `initialize()` function provides no indication that a follow-up call is required, and there is no on-chain enforcement preventing the contract from being left in this broken state. Existing mainnet deployments are unaffected because the relevant reinitializer was already executed, but the risk is real for every future deployment.

## Recommendation
Set a non-zero default for `dailyMintLimit` directly inside `initialize()`, or add a guard in `limitDailyMint` that treats `0` as uncapped:

```solidity
// Option A – initialize with a safe default
dailyMintLimit = type(uint256).max;

// Option B – treat 0 as uncapped in the modifier
if (dailyMintLimit != 0 && dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
```

Apply the same fix to `RSETHPoolV3ExternalBridge.sol`.

## Proof of Concept
1. Deploy `RSETHPoolV3` behind a proxy and call `initialize(admin, bridger, wrsETH, feeBps, oracle, true)`.
2. Do **not** call `reinitialize(2)` or `setDailyMintLimit()`.
3. Call `deposit{value: 1 ether}("")` from any EOA.
4. Transaction reverts with `DailyMintLimitExceeded` because `dailyMintLimit == 0` and `rsETHAmount > 0`.
5. Call `setDailyMintLimit(100 ether)` as admin; repeat step 3 — deposit succeeds.

Foundry test sketch:
```solidity
function test_depositRevertsWhenDailyMintLimitUninitialized() public {
    // Deploy proxy, call only initialize()
    RSETHPoolV3 pool = new RSETHPoolV3();
    // ... proxy setup, initialize() only ...
    vm.deal(user, 1 ether);
    vm.prank(user);
    vm.expectRevert(RSETHPoolV3.DailyMintLimitExceeded.selector);
    pool.deposit{value: 1 ether}("");
}
```

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-121)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }
```

**File:** contracts/pools/RSETHPoolV3.sol (L179-198)
```text
    function reinitialize(
        uint256 _dailyMintLimit,
        uint256 _startTimestamp
    )
        external
        reinitializer(2)
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (_dailyMintLimit == 0) {
            revert InvalidDailyMintLimit();
        }

        // startTimestamp cannot be in the past
        if (block.timestamp > _startTimestamp) {
            revert InvalidStartTimestamp();
        }

        dailyMintLimit = _dailyMintLimit;
        startTimestamp = _startTimestamp;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L207-232)
```text
    function initialize(
        address admin,
        address bridger,
        address _wrsETH,
        uint256 _feeBps,
        address _rsETHOracle,
        bool _isEthDepositEnabled
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
        _setupRole(BRIDGER_ROLE, bridger);

        wrsETH = IERC20WrsETH(_wrsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
        isEthDepositEnabled = _isEthDepositEnabled;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L246-252)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-281)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
```

**File:** contracts/pools/RSETHPoolV3.sol (L605-611)
```text
    function setDailyMintLimit(uint256 _dailyMintLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_dailyMintLimit == 0) {
            revert InvalidDailyMintLimit();
        }
        dailyMintLimit = _dailyMintLimit;
        emit DailyMintLimitSet(_dailyMintLimit);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L153-155)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
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
