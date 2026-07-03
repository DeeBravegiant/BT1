### Title
Missing Oracle Rate Validation in `setRSETHOracle()` Causes Division-by-Zero, Freezing All Deposits — (`contracts/pools/RSETHPoolV3.sol`)

---

### Summary

`RSETHPoolV3.setRSETHOracle()` accepts any non-zero address as the new rsETH oracle without verifying that the oracle returns a non-zero rate. If a zero-returning oracle is set (e.g., a misconfigured or uninitialized oracle contract), every call to `deposit()` reverts with a division-by-zero panic, freezing all user deposits until a corrective TIMELOCK_ROLE transaction is executed.

---

### Finding Description

`RSETHPoolV3` validates oracle rates when adding token oracles via `addSupportedToken()` and `setSupportedTokenOracle()`, but applies no equivalent check when updating the primary rsETH oracle via `setRSETHOracle()`.

**`setRSETHOracle()` — missing rate validation:** [1](#0-0) 

```solidity
function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(_rsETHOracle);
    rsETHOracle = _rsETHOracle;
    emit OracleSet(_rsETHOracle);
}
```

Only a non-zero address check is performed. No call to `IOracle(_rsETHOracle).getRate()` is made.

**Contrast with `addSupportedToken()` — rate IS validated:** [2](#0-1) 

```solidity
if (IOracle(oracle).getRate() == 0) {
    revert UnsupportedOracle();
}
```

And `setSupportedTokenOracle()` also validates the rate before storing: [3](#0-2) 

```solidity
if (IOracle(oracle).getRate() == 0) {
    revert UnsupportedOracle();
}
```

**Downstream division-by-zero in `viewSwapRsETHAmountAndFee()`:** [4](#0-3) 

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // ← returns 0 from bad oracle
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;  // ← division by zero
}
```

This function is called inside the `limitDailyMint` modifier, which gates every `deposit()` call: [5](#0-4) 

```solidity
modifier limitDailyMint(uint256 amount, address token) {
    ...
    if (token == ETH_IDENTIFIER) {
        (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);   // ← panics here
    } else {
        (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
    }
    ...
}
```

Both `deposit(string)` (ETH) and `deposit(address,uint256,string)` (token) apply this modifier: [6](#0-5) 

---

### Impact Explanation

If `setRSETHOracle()` is called with an oracle that returns `0` from `getRate()` (e.g., a newly deployed but uninitialized oracle, or a contract whose underlying feed has not yet been seeded), every subsequent `deposit()` call panics with a division-by-zero error. No user can deposit ETH or any supported token into the pool. All deposits are frozen until TIMELOCK_ROLE executes a corrective `setRSETHOracle()` call with a valid oracle.

**Impact class**: Temporary freezing of funds (user deposits).

---

### Likelihood Explanation

The TIMELOCK_ROLE is a privileged but operationally active role responsible for oracle management. A realistic scenario is a routine oracle upgrade where the replacement oracle contract is deployed but not yet initialized (e.g., its underlying Chainlink feed or rate source has not been seeded). The protocol already demonstrates awareness of this risk by validating `getRate() == 0` in `addSupportedToken()` and `setSupportedTokenOracle()`. The omission in `setRSETHOracle()` is an inconsistency that makes a misconfiguration-induced freeze plausible during any oracle rotation.

---

### Recommendation

Add the same rate validation to `setRSETHOracle()` that is already applied to token oracle setters:

```solidity
function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(_rsETHOracle);
    if (IOracle(_rsETHOracle).getRate() == 0) {
        revert UnsupportedOracle();
    }
    rsETHOracle = _rsETHOracle;
    emit OracleSet(_rsETHOracle);
}
```

---

### Proof of Concept

1. TIMELOCK_ROLE deploys a new oracle contract `BadOracle` whose `getRate()` returns `0` (e.g., uninitialized Chainlink aggregator wrapper).
2. TIMELOCK_ROLE calls `RSETHPoolV3.setRSETHOracle(address(BadOracle))`. The call succeeds — only the non-zero address check is applied.
3. Any user calls `deposit{value: 1 ether}("ref")`.
4. The `limitDailyMint` modifier calls `viewSwapRsETHAmountAndFee(1 ether)`.
5. `getRate()` returns `0`; the line `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` panics with `Panic(0x12)` (division by zero).
6. All ETH and token deposits revert. The pool is frozen until TIMELOCK_ROLE sets a valid oracle.

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

**File:** contracts/pools/RSETHPoolV3.sol (L533-537)
```text
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L548-550)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```

**File:** contracts/pools/RSETHPoolV3.sol (L584-586)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```
