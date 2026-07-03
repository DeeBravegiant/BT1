### Title
`viewSwapRsETHAmountAndFee()` Missing Pause and Daily-Mint-Limit Checks Present in `deposit()` — (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

---

### Summary

The public view function `viewSwapRsETHAmountAndFee()` in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` returns a seemingly valid wrsETH quote without enforcing the same guards that the actual `deposit()` function applies. Specifically, `deposit()` enforces `whenNotPaused` and the `limitDailyMint` modifier (which checks `block.timestamp < startTimestamp` and `dailyMintAmount + rsETHAmount > dailyMintLimit`), while `viewSwapRsETHAmountAndFee()` enforces none of these. Any caller — user, aggregator, or on-chain router — that relies on the view function to confirm a deposit is executable will receive a valid-looking quote that will revert when the actual deposit is attempted.

---

### Finding Description

In `RSETHPoolV3`, the public view function `viewSwapRsETHAmountAndFee(uint256 amount)` computes and returns a wrsETH quote purely from the oracle rate and fee:

```solidity
// contracts/pools/RSETHPoolV3.sol lines 299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

It carries no `whenNotPaused` guard and no `limitDailyMint` guard.

The actual `deposit()` function, however, applies both:

```solidity
// contracts/pools/RSETHPoolV3.sol lines 246-265
function deposit(string memory referralId)
    external payable nonReentrant
    whenNotPaused                          // ← absent in view function
    limitDailyMint(msg.value, ETH_IDENTIFIER)  // ← absent in view function
{
    ...
}
```

The `limitDailyMint` modifier enforces two additional conditions:

```solidity
// contracts/pools/RSETHPoolV3.sol lines 96-125
modifier limitDailyMint(uint256 amount, address token) {
    if (block.timestamp < startTimestamp) {
        revert MintBeforeStartTimestamp();
    }
    ...
    if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
        revert DailyMintLimitExceeded();
    }
    ...
}
```

The identical gap exists in `RSETHPoolV3ExternalBridge` at the same structural positions.

---

### Impact Explanation

Any caller that uses `viewSwapRsETHAmountAndFee()` as a pre-flight check — including on-chain routers, aggregators, or front-end simulations — will receive a non-zero, non-reverting quote in all three blocked states:

1. **Contract paused** — `deposit()` reverts with `ContractPaused`; view function returns a valid quote.
2. **Before `startTimestamp`** — `deposit()` reverts with `MintBeforeStartTimestamp`; view function returns a valid quote.
3. **Daily mint limit exhausted** — `deposit()` reverts with `DailyMintLimitExceeded`; view function returns a valid quote.

In each case the contract fails to deliver the promised return (the quoted wrsETH amount) without losing user value. Impact: **Low — contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The daily mint limit is an active, recurring control: once the limit is reached for a given day, every subsequent call to `viewSwapRsETHAmountAndFee()` still returns a valid quote while every `deposit()` reverts. This condition is reachable by any unprivileged depositor on any day the limit is hit, making it a regularly occurring discrepancy rather than a one-time edge case.

---

### Recommendation

Add the same guards to `viewSwapRsETHAmountAndFee()` that `deposit()` enforces, or document clearly that the view function does not reflect executability. At minimum, the function should revert (or return zero) when `paused == true`, when `block.timestamp < startTimestamp`, or when the daily mint limit would be exceeded by the requested amount. The `remainingDailyMintLimit()` helper already exposes the correct remaining capacity and can be used for this check.

---

### Proof of Concept

1. The daily mint limit is reached (`dailyMintAmount == dailyMintLimit`).
2. An on-chain router calls `viewSwapRsETHAmountAndFee(1 ether)` → receives `(rsETHAmount, fee)` with `rsETHAmount > 0`, no revert.
3. The router proceeds to call `deposit{value: 1 ether}(referralId)`.
4. The `limitDailyMint` modifier evaluates `dailyMintAmount + rsETHAmount > dailyMintLimit` → `true` → reverts with `DailyMintLimitExceeded`.
5. The router's transaction reverts; the user's ETH is returned but gas is wasted and the router's logic is broken.

The same sequence applies when `paused == true` (step 4 reverts with `ContractPaused`) or when `block.timestamp < startTimestamp` (step 4 reverts with `MintBeforeStartTimestamp`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
