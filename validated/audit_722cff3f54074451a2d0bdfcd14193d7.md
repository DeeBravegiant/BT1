### Title
Missing `_feeBps` Upper-Bound Sanity Check in `initialize()` Allows 100% Fee Configuration, Causing Depositors to Receive Zero rsETH - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

Multiple L2 pool contracts accept `_feeBps` in their `initialize()` function without any upper-bound validation, while the corresponding `setFeeBps()` setter enforces a strict cap. If `_feeBps = 10_000` (100%) is passed during initialization — a plausible mistake given the lack of decimal-awareness — every subsequent depositor sends ETH or tokens to the pool and receives **zero** rsETH/wrsETH in return. The entire deposited amount is silently classified as protocol fees and becomes withdrawable by the `BRIDGER_ROLE`, constituting a direct loss of user funds.

---

### Finding Description

In `RSETHPoolV3.initialize()`, `_feeBps` is stored without any validation:

```solidity
// contracts/pools/RSETHPoolV3.sol:229
feeBps = _feeBps;
``` [1](#0-0) 

Yet the post-deployment setter enforces a hard cap of 10% (1000 bps):

```solidity
// contracts/pools/RSETHPoolV3.sol:518-519
function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_feeBps > 1000) revert InvalidFeeAmount();
``` [2](#0-1) 

The same asymmetry exists in `RSETHPoolV3ExternalBridge`, `RSETHPoolNoWrapper`, `RSETHPoolV2ExternalBridge`, and `RSETHPoolV2` — all accept an unchecked `_feeBps` in `initialize()`. [3](#0-2) [4](#0-3) 

When `feeBps = 10_000`, the swap calculation in `viewSwapRsETHAmountAndFee` produces:

```solidity
fee = amount * 10_000 / 10_000;   // = amount (100%)
uint256 amountAfterFee = amount - fee;  // = 0
rsETHAmount = 0 * 1e18 / rsETHToETHrate; // = 0
``` [5](#0-4) 

Back in `deposit()`:

```solidity
feeEarnedInETH += fee;          // entire deposit classified as fee
wrsETH.mint(msg.sender, 0);     // user receives nothing
``` [6](#0-5) 

The `limitDailyMint` modifier does **not** protect users: when `rsETHAmount = 0`, the check `dailyMintAmount + 0 > dailyMintLimit` evaluates to false and the deposit proceeds silently. [7](#0-6) 

Critically, the `deposit()` function accepts no `minRSETHAmountExpected` slippage guard (unlike `LRTDepositPool.depositETH()`), so there is no on-chain protection for the depositor. [8](#0-7) 

The entire deposited ETH, now recorded in `feeEarnedInETH`, is withdrawable by the `BRIDGER_ROLE` via `withdrawFees()`:

```solidity
uint256 amountToSendInETH = feeEarnedInETH;
feeEarnedInETH = 0;
(bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
``` [9](#0-8) 

In `RSETHPoolNoWrapper`, the impact is identical: `rsETH.safeTransfer(msg.sender, 0)` transfers nothing while the deposited ETH is fully absorbed into `feeEarnedInETH`. [10](#0-9) 

---

### Impact Explanation

Every user who calls `deposit()` on a pool initialized with `feeBps = 10_000`:
- Sends ETH (or tokens) to the contract.
- Receives **zero** rsETH/wrsETH.
- Has no on-chain recourse (no slippage guard, no refund path).
- Their ETH is permanently classified as protocol fees and drainable by `BRIDGER_ROLE`.

This constitutes **direct theft of user funds** (Critical) or at minimum **permanent freezing of user funds** (Critical), depending on whether the fee receiver withdraws the balance.

---

### Likelihood Explanation

The scenario requires the deployer to pass `_feeBps = 10_000` during initialization. This is a realistic mistake: a deployer unfamiliar with basis-point conventions might enter `10000` intending "1%" or "10%" rather than "100%". The M-05 reference report documents exactly this class of decimal-awareness error in real deployments. The inconsistency between `initialize()` (no check) and `setFeeBps()` (hard cap at 1000 bps in `RSETHPoolV3`) makes the mistake more likely because the setter's cap is never surfaced at deployment time.

---

### Recommendation

Apply the same upper-bound validation in `initialize()` as in `setFeeBps()`. For `RSETHPoolV3`:

```solidity
function initialize(..., uint256 _feeBps, ...) external initializer {
    if (_feeBps > 1000) revert InvalidFeeAmount(); // same cap as setFeeBps
    ...
    feeBps = _feeBps;
}
```

Apply the analogous check in `RSETHPoolV3ExternalBridge`, `RSETHPoolNoWrapper`, `RSETHPoolV2ExternalBridge`, and `RSETHPoolV2`.

---

### Proof of Concept

1. Deploy `RSETHPoolV3` proxy and call `initialize(admin, bridger, wrsETH, 10_000, rsETHOracle, true)`.
2. Call `reinitialize(dailyMintLimit, startTimestamp)` to enable deposits.
3. User calls `deposit{value: 1 ether}("")`.
4. Inside `viewSwapRsETHAmountAndFee(1e18)`:
   - `fee = 1e18 * 10_000 / 10_000 = 1e18`
   - `amountAfterFee = 0`
   - `rsETHAmount = 0`
5. `feeEarnedInETH += 1e18` — user's 1 ETH is now a "fee".
6. `wrsETH.mint(msg.sender, 0)` — user receives 0 wrsETH.
7. `BRIDGER_ROLE` calls `withdrawFees(receiver)` and receives the user's 1 ETH.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-124)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
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

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L453-461)
```text
    function withdrawFees(address receiver) external nonReentrant onlyRole(BRIDGER_ROLE) {
        // withdraw fees in ETH
        uint256 amountToSendInETH = feeEarnedInETH;
        feeEarnedInETH = 0;
        (bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
        if (!success) revert TransferFailed();

        emit FeesWithdrawn(amountToSendInETH);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L518-521)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L190-217)
```text
    function initialize(
        address admin,
        address manager,
        address _rsETH,
        uint256 _feeBps,
        address _rsETHOracle,
        bool _isEthDepositEnabled
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(admin);
        UtilLib.checkNonZeroAddress(manager);
        UtilLib.checkNonZeroAddress(_rsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        __AccessControl_init();
        __Pausable_init();
        __ReentrancyGuard_init();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(BRIDGER_ROLE, manager);

        rsETH = IERC20(_rsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
        isEthDepositEnabled = _isEthDepositEnabled;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
