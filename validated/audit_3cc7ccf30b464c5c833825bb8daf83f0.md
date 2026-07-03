### Title
`viewSwapRsETHAmountAndFee` Returns Non-Zero Quotes When Deposits Would Revert Due to Paused State, Daily Mint Limit, or Disabled ETH Deposits - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

The `viewSwapRsETHAmountAndFee` view functions across all L2 pool contracts are intended to let users preview how much wrsETH they will receive for a given deposit. However, these functions do not enforce the same guards as the actual `deposit` functions (`whenNotPaused`, `limitDailyMint`, `isEthDepositEnabled`, `startTimestamp`). As a result, the quote functions return positive, non-zero amounts even when the corresponding deposit would revert entirely.

---

### Finding Description

In `RSETHPoolV3.sol`, the `deposit(string)` function is guarded by three conditions that `viewSwapRsETHAmountAndFee(uint256)` does not replicate:

**Actual `deposit` guards:** [1](#0-0) 

1. `whenNotPaused` — reverts if `paused == true`
2. `limitDailyMint` — reverts if `block.timestamp < startTimestamp` or if `dailyMintAmount + rsETHAmount > dailyMintLimit`
3. `if (!isEthDepositEnabled) revert EthDepositDisabled()` — reverts if ETH deposits are disabled

**The quote function:** [2](#0-1) 

`viewSwapRsETHAmountAndFee` only computes `fee` and `rsETHAmount` from the oracle rate. It checks none of the above conditions. The same pattern exists in:

- `RSETHPoolV3ExternalBridge.sol` — `viewSwapRsETHAmountAndFee` at lines 418–427 and 433–453, while `deposit` at lines 366–384 and 390–412 enforces `whenNotPaused` and `limitDailyMint` [3](#0-2) 

- `RSETHPoolV3WithNativeChainBridge.sol` — same pattern [4](#0-3) 

- `RSETHPoolV2.sol` — `viewSwapRsETHAmountAndFee` at lines 225–234 does not check `paused`, `dailyMintLimit`, or `startTimestamp` [5](#0-4) 

The `limitDailyMint` modifier itself shows the conditions that are enforced during actual deposits but absent from the view function: [6](#0-5) 

---

### Impact Explanation

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user or off-chain integrator calling `viewSwapRsETHAmountAndFee` receives a positive wrsETH quote. They then submit a `deposit` transaction that reverts because:
- The contract is paused (emergency scenario)
- The daily mint cap is already exhausted for the current day
- ETH deposits are administratively disabled
- The `startTimestamp` has not yet been reached

The user loses gas but not principal (the deposit reverts). However, the view function's promise is broken: it advertises a deliverable amount that the protocol cannot actually fulfill at that moment. Automated integrators (e.g., aggregators, routing contracts) that rely on a non-zero return from `viewSwapRsETHAmountAndFee` as a signal to proceed will submit failing transactions.

---

### Likelihood Explanation

**Likelihood: Medium.**

All three blocking conditions are operationally realistic:
- The `paused` flag is a live emergency mechanism used by the `PAUSER_ROLE`.
- The `dailyMintLimit` is an active feature (introduced via `reinitializer(2)` in V2 and `reinitializer(2)` in V3) and will be hit whenever deposit volume is high.
- `isEthDepositEnabled` is a configurable flag that can be toggled by the admin.

Any of these conditions can be active while a user or integrator queries `viewSwapRsETHAmountAndFee` to decide whether to deposit.

---

### Recommendation

Add the same state checks to `viewSwapRsETHAmountAndFee` that are enforced in `deposit`:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    // Mirror deposit guards
    if (paused) revert ContractPaused();
    if (!isEthDepositEnabled) revert EthDepositDisabled();
    if (block.timestamp < startTimestamp) revert MintBeforeStartTimestamp();

    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;

    // Check daily limit
    uint256 currentDay = getCurrentDay();
    uint256 effectiveDailyMintAmount = (currentDay > lastMintDay) ? 0 : dailyMintAmount;
    if (effectiveDailyMintAmount + rsETHAmount > dailyMintLimit) revert DailyMintLimitExceeded();
}
```

Apply the same pattern to the token-variant overload and to all affected pool contracts (`RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV2`, `RSETHPoolV2ExternalBridge`).

---

### Proof of Concept

1. Admin calls `pause()` on `RSETHPoolV3`, setting `paused = true`. [7](#0-6) 

2. User calls `viewSwapRsETHAmountAndFee(1 ether)`. The function returns `(rsETHAmount > 0, fee > 0)` — no revert, no indication that the deposit is blocked. [2](#0-1) 

3. User submits `deposit{value: 1 ether}("")`. The `whenNotPaused` modifier reverts with `ContractPaused`. [1](#0-0) 

4. Same scenario applies when `dailyMintAmount + rsETHAmount > dailyMintLimit` (daily cap exhausted) or `isEthDepositEnabled == false`. In all cases, `viewSwapRsETHAmountAndFee` returns a positive quote while `deposit` reverts. [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L71-74)
```text
    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }
```

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

**File:** contracts/pools/RSETHPoolV3.sol (L246-253)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L335-344)
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

**File:** contracts/pools/RSETHPoolV2.sol (L225-234)
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
