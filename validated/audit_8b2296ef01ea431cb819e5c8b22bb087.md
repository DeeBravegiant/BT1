### Title
Missing Daily Mint Limit in `RSETHPoolNoWrapper.deposit()` Allows Unrestricted Pool Drain — (File: contracts/pools/RSETHPoolNoWrapper.sol)

---

### Summary

`RSETHPoolNoWrapper.deposit()` (both the ETH and token variants) lacks the `limitDailyMint` safety modifier that `RSETHPoolV3.deposit()` enforces. Any unprivileged depositor can drain the pool's entire pre-minted rsETH balance in a single transaction, temporarily preventing all other users from swapping on that chain.

---

### Finding Description

`RSETHPoolV3` enforces a daily minting ceiling via the `limitDailyMint` modifier on every public deposit entry point:

```solidity
// RSETHPoolV3.sol – ETH deposit
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)   // ← safety gate
{ ... }

// RSETHPoolV3.sol – token deposit
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused
    onlySupportedToken(token)
    limitDailyMint(amount, token)               // ← safety gate
{ ... }
``` [1](#0-0) [2](#0-1) 

`RSETHPoolNoWrapper`, which serves chains where rsETH is a native OFT (e.g. Arbitrum, Unichain), has **no such modifier** on either deposit function:

```solidity
// RSETHPoolNoWrapper.sol – ETH deposit (no limitDailyMint)
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    ...
    rsETH.safeTransfer(msg.sender, rsETHAmount);   // transfers pool's entire balance if large enough
}

// RSETHPoolNoWrapper.sol – token deposit (no limitDailyMint)
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token)
{
    ...
    rsETH.safeTransfer(msg.sender, rsETHAmount);
}
``` [3](#0-2) [4](#0-3) 

`RSETHPoolNoWrapper` also contains no `dailyMintLimit`, `dailyMintAmount`, `lastMintDay`, or `startTimestamp` state variables, confirming the protection is entirely absent rather than merely misconfigured.

The daily limit exists precisely to guard against oracle-rate drift and to prevent a single actor from consuming the pool's liquidity in one block. Without it, a depositor who sends a sufficiently large ETH (or token) amount in a single call will receive all available rsETH, leaving the pool empty.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Once the pool's rsETH balance reaches zero, every subsequent `deposit` call reverts with an ERC-20 insufficient-balance error. All other users on that L2 chain are unable to swap ETH/LSTs for rsETH until the BRIDGER_ROLE manually replenishes the pool. The freeze persists for as long as it takes the operator to bridge new rsETH back into the contract, which can span hours to days depending on bridge latency and operator response time. [5](#0-4) 

---

### Likelihood Explanation

**Medium.** The attack requires only a single `deposit` call with enough ETH to exhaust the pool's rsETH balance. No special role, flash loan, or oracle manipulation is needed. The pool's rsETH balance is publicly readable on-chain, so an attacker can calculate the exact ETH amount required before executing. The cost to the attacker is the ETH deposited (received back as rsETH at the oracle rate), making the attack nearly free if the attacker intends to hold rsETH anyway.

---

### Recommendation

Add the same daily-mint-limit mechanism to `RSETHPoolNoWrapper`. Either:

1. Port the `limitDailyMint` modifier and its associated state variables (`dailyMintLimit`, `dailyMintAmount`, `lastMintDay`, `startTimestamp`) into `RSETHPoolNoWrapper` and apply it to both `deposit` overloads, or
2. Introduce a per-transaction cap (e.g. `maxSwapAmount`) as a simpler alternative that prevents single-block drains. [6](#0-5) 

---

### Proof of Concept

1. Attacker reads `rsETH.balanceOf(address(RSETHPoolNoWrapper))` → `poolBalance`.
2. Attacker computes the ETH amount `X` such that `viewSwapRsETHAmountAndFee(X).rsETHAmount == poolBalance`.
3. Attacker calls `RSETHPoolNoWrapper.deposit{value: X}("")`.
4. No daily-limit check fires; `rsETH.safeTransfer(attacker, poolBalance)` succeeds.
5. Pool rsETH balance is now 0; every subsequent user `deposit` call reverts.
6. Users on that L2 chain cannot swap ETH for rsETH until an operator bridges new rsETH into the pool. [3](#0-2) [7](#0-6)

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-271)
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
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```
