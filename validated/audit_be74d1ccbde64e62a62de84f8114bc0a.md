### Title
Uninitialized `dailyMintLimit` Permanently Blocks All Deposits Until Separate Reinitializer Is Called - (File: `contracts/pools/RSETHPoolV3.sol`)

---

### Summary

`RSETHPoolV3.initialize()` does not set `dailyMintLimit`, leaving it at its Solidity default of `0`. The `limitDailyMint` modifier applied to both `deposit` functions unconditionally reverts with `DailyMintLimitExceeded` for any non-zero deposit when `dailyMintLimit == 0`. A separate `reinitialize()` function (marked `reinitializer(2)`) must be called by the admin to set a non-zero limit. Until that call is made, the contract is deployed in a state where no user can deposit any asset.

---

### Finding Description

`initialize` sets up roles, the wrsETH token, fee basis points, and the oracle, but does not set `dailyMintLimit` or `startTimestamp`. Both default to `0`. [1](#0-0) 

The `limitDailyMint` modifier enforces:

```solidity
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
``` [2](#0-1) 

With `dailyMintLimit == 0`, any deposit of any non-zero amount produces `rsETHAmount > 0`, making `0 + rsETHAmount > 0` always true. Every call to `deposit(string)` (ETH) and `deposit(address, uint256, string)` (ERC20) reverts before any token transfer occurs. [3](#0-2) [4](#0-3) 

The only path to set a non-zero `dailyMintLimit` is `reinitialize`: [5](#0-4) 

This function is presented as an upgrade-only reinitializer (`reinitializer(2)`), not as a mandatory post-deployment step. There is no guard in `initialize` or in the deposit functions that communicates this dependency. Critically, `setDailyMintLimit` explicitly rejects zero values: [6](#0-5) 

This confirms that `0` is an invalid operational state for `dailyMintLimit`, yet the contract is deployed into exactly that state. The design implies `reinitialize` is an optional upgrade step, but it is actually mandatory for the contract to function — a direct analog to the Crowdsale's `finalizeAgent` described as optional while being required to exit `Preparing` state.

---

### Impact Explanation

Any user attempting to deposit ETH or a supported ERC20 token into `RSETHPoolV3` will have their transaction revert with `DailyMintLimitExceeded` until `reinitialize` is called. The contract fails to deliver its core promised function (minting wrsETH in exchange for deposited assets). No user funds are lost because the revert occurs before any token transfer, but the contract is entirely non-functional for its primary purpose.

**Impact: Low** — Contract fails to deliver promised returns, but doesn't lose value.

---

### Likelihood Explanation

This affects every fresh deployment of `RSETHPoolV3` before `reinitialize` is called. The window of non-functionality exists between contract deployment and the admin calling `reinitialize`. Because `reinitialize` uses the `reinitializer(2)` pattern (an upgrade idiom), operators may not recognize it as a mandatory post-deployment step, extending this window indefinitely. The entry path is fully unprivileged: any depositor triggers the failure.

---

### Recommendation

Set a non-zero default for `dailyMintLimit` directly in `initialize`, or require it as a constructor/initializer parameter. Alternatively, add an explicit `require(dailyMintLimit > 0)` guard at the top of the `limitDailyMint` modifier with a descriptive error, so the failure mode is immediately obvious rather than silently masquerading as a limit-exceeded condition.

---

### Proof of Concept

1. Deploy `RSETHPoolV3` and call `initialize(admin, bridger, wrsETH, feeBps, oracle, true)`.
2. Do **not** call `reinitialize`. `dailyMintLimit` remains `0`.
3. Call `deposit{value: 1 ether}("")` as any user.
4. Transaction reverts with `DailyMintLimitExceeded` because `0 + rsETHAmount > 0`.
5. Call `reinitialize(1000 ether, block.timestamp + 1)` as admin.
6. Call `deposit{value: 1 ether}("")` again — succeeds.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-121)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }
```

**File:** contracts/pools/RSETHPoolV3.sol (L176-198)
```text
    /// @dev Reinitializer function to set the daily minting limit
    /// @param _dailyMintLimit The daily minting limit
    /// @param _startTimestamp The start timestamp for the daily minting limit
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

**File:** contracts/pools/RSETHPoolV3.sol (L604-611)
```text
    /// @param _dailyMintLimit The new daily minting limit
    function setDailyMintLimit(uint256 _dailyMintLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_dailyMintLimit == 0) {
            revert InvalidDailyMintLimit();
        }
        dailyMintLimit = _dailyMintLimit;
        emit DailyMintLimitSet(_dailyMintLimit);
    }
```
