### Title
Missing `feeBps` Validation in `initialize` Allows Fee to Exceed the Cap Enforced by `setFeeBps` — (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

`RSETHPoolV3.sol`'s `initialize` function sets `feeBps` without applying the same upper-bound check that `setFeeBps` enforces. This is the direct analog of the River.1.sol finding in the external report: the initializer does not follow the same validation as the corresponding setter. If the pool is deployed with `feeBps` above the intended cap (1000 bps = 10%), every depositor receives fewer wrsETH tokens than the protocol intends — and at `feeBps = 10000` (100%), depositors receive zero wrsETH while their entire ETH deposit is silently redirected to `feeEarnedInETH`.

---

### Finding Description

`setFeeBps` enforces a hard cap of 1000 bps (10%):

```solidity
// contracts/pools/RSETHPoolV3.sol line 518-521
function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_feeBps > 1000) revert InvalidFeeAmount();
    feeBps = _feeBps;
    emit FeeBpsSet(_feeBps);
}
``` [1](#0-0) 

The `initialize` function, however, assigns `feeBps` directly with no validation:

```solidity
// contracts/pools/RSETHPoolV3.sol line 207-232
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
    ...
    feeBps = _feeBps;   // ← no cap check
    ...
}
``` [2](#0-1) 

The same gap exists in `RSETHPoolV2ExternalBridge.sol` and `RSETHPoolV3WithNativeChainBridge.sol`, both of which also cap `setFeeBps` at 1000 bps but leave `initialize` unchecked. [3](#0-2) [4](#0-3) 

The fee is applied inside `viewSwapRsETHAmountAndFee`, which is called by every deposit path:

```solidity
// contracts/pools/RSETHPoolV3.sol line 299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [5](#0-4) 

At `feeBps = 10000`: `fee = amount`, `amountAfterFee = 0`, `rsETHAmount = 0`. The depositor's entire ETH is captured as `feeEarnedInETH` and the depositor receives zero wrsETH.

---

### Impact Explanation

Every user who calls `deposit` on a pool initialized with `feeBps > 1000` is overcharged relative to the protocol's own stated cap. At the extreme (`feeBps = 10000`), the depositor's full ETH balance is absorbed into `feeEarnedInETH` and the depositor receives zero wrsETH — a complete loss of deposited principal. The ETH is not destroyed; it accumulates in the pool and is withdrawable only by the `BRIDGER_ROLE`, making it permanently inaccessible to the depositor. This constitutes direct theft of user funds.

**Impact: Critical — direct theft of depositor funds.**

---

### Likelihood Explanation

The pool is an upgradeable proxy. The `initialize` function is called exactly once at deployment time by the deployer. A deployment script that passes `_feeBps` without range-checking it (e.g., a misconfigured value, a unit confusion between bps and percent, or a copy-paste error) would silently deploy a pool that steals from every depositor. Because the setter enforces the cap but the initializer does not, the inconsistency is a latent trap that is invisible to users inspecting only the setter. Likelihood is low but non-negligible given the number of pool variants deployed across chains.

---

### Recommendation

Apply the same validation in `initialize` as in `setFeeBps`:

```solidity
if (_feeBps > 1000) revert InvalidFeeAmount();
feeBps = _feeBps;
```

Apply the same fix to `RSETHPoolV2ExternalBridge.sol` and `RSETHPoolV3WithNativeChainBridge.sol`.

---

### Proof of Concept

1. Deploy `RSETHPoolV3` proxy and call `initialize` with `_feeBps = 10000`.
2. Call `reinitialize(dailyMintLimit, startTimestamp)` to enable deposits.
3. An unprivileged user calls `deposit("ref")` with `msg.value = 1 ether`.
4. Inside `viewSwapRsETHAmountAndFee(1 ether)`:
   - `fee = 1 ether * 10000 / 10000 = 1 ether`
   - `amountAfterFee = 0`
   - `rsETHAmount = 0`
5. `wrsETH.mint(msg.sender, 0)` — user receives 0 wrsETH.
6. `feeEarnedInETH += 1 ether` — user's 1 ETH is now locked as protocol fees.
7. Only the `BRIDGER_ROLE` can call `withdrawFees`, so the user has no recourse. [6](#0-5) [7](#0-6)

### Citations

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
