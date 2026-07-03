### Title
Uninitialized `dailyMintLimit` in `RSETHPoolV3` Causes `limitDailyMint` Modifier to Always Revert, Blocking All Deposits - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The `dailyMintLimit` state variable is never set inside `initialize()`. It defaults to `0`. The `limitDailyMint` modifier, which guards every `deposit()` entry point, evaluates `dailyMintAmount + rsETHAmount > dailyMintLimit` — with `dailyMintLimit == 0` this condition is always `true` for any non-zero deposit, causing every deposit call to revert with `DailyMintLimitExceeded`. The same defect exists in `RSETHPoolV3ExternalBridge.sol`.

### Finding Description
`RSETHPoolV3.initialize()` sets `wrsETH`, `feeBps`, `rsETHOracle`, and `isEthDepositEnabled`, but leaves `dailyMintLimit` and `startTimestamp` at their Solidity defaults of `0`. [1](#0-0) 

`dailyMintLimit` is only written by `reinitialize(uint256,uint256)` (tagged `reinitializer(2)`) and by `setDailyMintLimit()`. Neither is called atomically with `initialize()`, so any freshly deployed proxy that has not yet executed `reinitialize(2)` or `setDailyMintLimit()` operates with `dailyMintLimit == 0`. [2](#0-1) 

The `limitDailyMint` modifier applied to both `deposit()` overloads performs:

```solidity
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
```

With `dailyMintLimit == 0`, the inequality `rsETHAmount > 0` is always satisfied for any real deposit, so the modifier unconditionally reverts. [3](#0-2) 

Both `deposit(string)` (ETH) and `deposit(address,uint256,string)` (token) carry this modifier: [4](#0-3) [5](#0-4) 

The identical pattern exists in `RSETHPoolV3ExternalBridge.sol`: [6](#0-5) [7](#0-6) 

### Impact Explanation
Every user-facing deposit path on the L2 pool is gated by `limitDailyMint`. With `dailyMintLimit == 0`, no depositor — regardless of amount or asset — can mint wrsETH. The contract accepts no new value and delivers no promised returns until an admin separately calls `reinitialize(2)` or `setDailyMintLimit()`. No user funds are lost (the ETH reverts with the transaction), but the contract entirely fails to deliver its core function.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation
Any new proxy deployment of `RSETHPoolV3` (e.g., a new L2 chain onboarding) that calls only `initialize()` and omits the separate `reinitialize(2)` / `setDailyMintLimit()` step will be silently broken from day one. The existing mainnet deployment is unaffected because `reinitialize(2)` was already executed, but the risk is real for every future deployment.

### Recommendation
Set a non-zero default for `dailyMintLimit` directly inside `initialize()`, or add a guard in `limitDailyMint` that skips the cap check when `dailyMintLimit == 0` (treating zero as "uncapped"):

```solidity
// Option A – initialize with a safe default
dailyMintLimit = type(uint256).max; // or a protocol-chosen value

// Option B – treat 0 as uncapped in the modifier
if (dailyMintLimit != 0 && dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
```

Apply the same fix to `RSETHPoolV3ExternalBridge.sol`.

### Proof of Concept
1. Deploy `RSETHPoolV3` behind a proxy and call `initialize(admin, bridger, wrsETH, feeBps, oracle, true)`.
2. Do **not** call `reinitialize(2)` or `setDailyMintLimit()`.
3. Call `deposit{value: 1 ether}("")` from any EOA.
4. Transaction reverts with `DailyMintLimitExceeded` because `dailyMintLimit == 0` and `rsETHAmount > 0`.
5. Call `setDailyMintLimit(100 ether)` as admin; repeat step 3 — deposit succeeds.

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

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
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
