### Title
Uninitialized `dailyMintLimit` in `initialize()` Causes All User Deposits to Revert — (`contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary

`RSETHPoolV3WithNativeChainBridge` and `RSETHPoolV3ExternalBridge` do not set `dailyMintLimit` or `startTimestamp` in their `initialize()` functions. These values are only set via separate `reinitialize()` calls. If a reinitializer is not executed after deployment, `dailyMintLimit` remains `0`, causing every user deposit to revert with `DailyMintLimitExceeded` — a complete, externally-triggered DoS on the deposit path.

---

### Finding Description

`RSETHPoolV3WithNativeChainBridge.initialize()` sets `wrsETH`, `feeBps`, and `rsETHOracle`, but leaves `dailyMintLimit` and `startTimestamp` at their zero defaults. [1](#0-0) 

`dailyMintLimit` is only populated via `reinitialize(2)`: [2](#0-1) 

Every user-facing `deposit()` call applies the `limitDailyMint` modifier: [3](#0-2) 

The modifier enforces: [4](#0-3) 

With `startTimestamp = 0`, the first guard `block.timestamp < startTimestamp` is always `false` (any real timestamp exceeds 0), so it passes silently. With `dailyMintLimit = 0`, the second guard `dailyMintAmount + rsETHAmount > dailyMintLimit` reduces to `rsETHAmount > 0`, which is true for every non-zero deposit. The transaction reverts with `DailyMintLimitExceeded` before any state change occurs.

The identical pattern exists in `RSETHPoolV3ExternalBridge`, where `dailyMintLimit` is absent from `initialize()` and only set in `reinitialize(4)`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

All calls to `deposit(string)` and `deposit(address,uint256,string)` revert unconditionally until `reinitialize()` is called by the admin. No user can deposit ETH or supported tokens into the pool. ETH already held in the pool is not directly at risk (bridger functions do not use `limitDailyMint`), but the deposit surface is completely frozen. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

The `reinitialize()` functions are separate, post-`initialize()` transactions. For any new chain deployment of `RSETHPoolV3WithNativeChainBridge` or `RSETHPoolV3ExternalBridge`, the deployer must remember to call the reinitializer in a follow-up step. The version history of `RSETHPoolV3ExternalBridge` (six sequential reinitializers) demonstrates that this multi-step pattern is the established deployment practice, increasing the chance that a step is omitted on a new deployment. No attacker action is required — any user attempting to deposit triggers the revert.

---

### Recommendation

Pass `dailyMintLimit` and `startTimestamp` directly as parameters to `initialize()` in both `RSETHPoolV3WithNativeChainBridge` and `RSETHPoolV3ExternalBridge`, and validate them with non-zero checks (mirroring the existing `UtilLib.checkNonZeroAddress` pattern used for address parameters). This eliminates the dependency on a separate post-deployment call and ensures the contract is fully operational immediately after initialization. [7](#0-6) 

---

### Proof of Concept

1. Deploy `RSETHPoolV3WithNativeChainBridge` behind a proxy and call `initialize(admin, bridger, wrsETH, feeBps, rsETHOracle)`.
2. Do **not** call `reinitialize(dailyMintLimit, startTimestamp)`.
3. As any unprivileged user, call `deposit{value: 1 ether}("ref")`.
4. The `limitDailyMint` modifier executes: `startTimestamp = 0` so `block.timestamp < 0` is false; `dailyMintLimit = 0` so `rsETHAmount > 0` is true → revert `DailyMintLimitExceeded`.
5. All deposits from all users revert until the admin calls `reinitialize(2)`. [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L108-137)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L218-237)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-300)
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
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L276-295)
```text
    function reinitialize(
        uint256 _dailyMintLimit,
        uint256 _startTimestamp
    )
        external
        reinitializer(4)
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

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
