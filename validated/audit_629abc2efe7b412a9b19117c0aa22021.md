### Title
Missing `_feeBps` Upper-Bound Validation in `RSETHPoolV3.initialize()` Enables Temporary Deposit Freeze or Excessive Fee Extraction — (`contracts/pools/RSETHPoolV3.sol`)

---

### Summary

`RSETHPoolV3.initialize()` sets `feeBps` from the caller-supplied `_feeBps` parameter with no upper-bound check. The post-deployment setter `setFeeBps()` enforces a hard cap of 1 000 bps (10 %), but that guard is absent from the initializer. An accidental misconfiguration at deployment time can either permanently revert every user deposit (if `_feeBps > 10 000`) or silently charge users an excessive fee (if `1 001 ≤ _feeBps ≤ 9 999`), with no way for users to detect or avoid the loss.

---

### Finding Description

`setFeeBps()` enforces:

```solidity
// contracts/pools/RSETHPoolV3.sol  line 518-521
function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_feeBps > 1000) revert InvalidFeeAmount();
    feeBps = _feeBps;
``` [1](#0-0) 

`initialize()` sets the same variable without any equivalent guard:

```solidity
// contracts/pools/RSETHPoolV3.sol  lines 207-232
function initialize(
    address admin,
    address bridger,
    address _wrsETH,
    uint256 _feeBps,          // ← no validation
    address _rsETHOracle,
    bool _isEthDepositEnabled
) external initializer {
    ...
    feeBps = _feeBps;         // ← stored unconditionally
``` [2](#0-1) 

`feeBps` is consumed in `viewSwapRsETHAmountAndFee()`:

```solidity
// contracts/pools/RSETHPoolV3.sol  lines 299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;   // ← underflows if feeBps > 10 000
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [3](#0-2) 

This view function is called inside the `limitDailyMint` modifier, which is applied to **both** `deposit(string)` (ETH) and `deposit(address,uint256,string)` (token) before any function body executes:

```solidity
// contracts/pools/RSETHPoolV3.sol  lines 96-125
modifier limitDailyMint(uint256 amount, address token) {
    ...
    if (token == ETH_IDENTIFIER) {
        (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
    } else {
        (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
    }
``` [4](#0-3) 

---

### Impact Explanation

**Scenario A — `_feeBps > 10 000` (e.g., accidentally passing a raw percentage like `5000` meaning "50 %" instead of `500` bps):**  
`fee = amount * feeBps / 10_000 > amount`, so `amount - fee` underflows and reverts in Solidity 0.8. Every call to `deposit()` reverts. The pool is completely frozen for all depositors until the admin calls `setFeeBps()` to correct the value. This is a **temporary freeze of funds** (Medium).

**Scenario B — `1 001 ≤ _feeBps ≤ 9 999`:**  
Deposits succeed but users receive far less `wrsETH` than the oracle rate implies. The excess ETH/tokens accumulate as `feeEarnedInETH` / `feeEarnedInToken` and are later withdrawn by `BRIDGER_ROLE`. Users have no on-chain protection against this. This is **contract fails to deliver promised returns** (Low).

---

### Likelihood Explanation

The `initialize()` function is called exactly once at deployment. The deployer must supply `_feeBps` as a raw `uint256`. The setter `setFeeBps()` documents the 1 000 bps cap via its revert, but the initializer provides no such signal. A deployer who passes a percentage integer (e.g., `50` for 50 %, or `5000` for 50 % in a different unit convention) instead of the correct basis-point value would silently misconfigure the contract. Because `feeBps` is not `immutable` the admin can later correct it, but only after users have already been harmed.

---

### Recommendation

Add the same guard to `initialize()` that exists in `setFeeBps()`:

```solidity
function initialize(
    address admin,
    address bridger,
    address _wrsETH,
    uint256 _feeBps,
    address _rsETHOracle,
    bool _isEthDepositEnabled
) external initializer {
    UtilLib.checkNonZeroAddress(_wrsETH);
    UtilLib.checkNonZeroAddress(_rsETHOracle);
+   if (_feeBps > 1000) revert InvalidFeeAmount();   // mirror setFeeBps guard
    ...
    feeBps = _feeBps;
```

Additionally, add zero-address checks for `admin` and `bridger`, which are also missing from `initialize()` while being validated in every post-deployment setter.

---

### Proof of Concept

1. Deploy `RSETHPoolV3` proxy and call `initialize(admin, bridger, wrsETH, 20000, oracle, true)` — `_feeBps = 20000` (200 %).
2. Any user calls `deposit{value: 1 ether}("ref")`.
3. The `limitDailyMint` modifier calls `viewSwapRsETHAmountAndFee(1 ether)`:
   - `fee = 1e18 * 20000 / 10000 = 2e18`
   - `amountAfterFee = 1e18 - 2e18` → **arithmetic underflow → revert**
4. Every deposit reverts. The pool is frozen for all users until the admin corrects `feeBps` via `setFeeBps()`. [5](#0-4) [3](#0-2)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
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

**File:** contracts/pools/RSETHPoolV3.sol (L518-521)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
```
