### Title
Shared `dailyMintAmount` Across All Tokens Allows One Token's Deposits to Exhaust the Daily Mint Limit for All Other Tokens - (File: `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary

The `limitDailyMint` modifier in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` uses a single shared `dailyMintAmount` counter for **all** deposit tokens (native ETH and every ERC20 in `supportedTokenList`). A large deposit of one token exhausts the shared counter, causing every subsequent `deposit()` call for any other token to revert with `DailyMintLimitExceeded` for the remainder of the day.

---

### Finding Description

`RSETHPoolV3` declares three contract-level (not per-token) state variables:

```solidity
uint256 public dailyMintLimit;   // single cap for all tokens
uint256 public dailyMintAmount;  // single accumulator for all tokens
uint256 public lastMintDay;      // single day tracker for all tokens
``` [1](#0-0) 

The `limitDailyMint` modifier, applied to **both** the ETH `deposit` and the ERC20 `deposit` overloads, reads and writes these shared variables regardless of which token is being deposited:

```solidity
modifier limitDailyMint(uint256 amount, address token) {
    ...
    if (currentDay > lastMintDay) {
        lastMintDay = currentDay;
        dailyMintAmount = 0;
    }
    if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
        revert DailyMintLimitExceeded();
    }
    dailyMintAmount += rsETHAmount;
    _;
}
``` [2](#0-1) 

Both deposit entry points apply this same modifier:

```solidity
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)   // ETH path
```

```solidity
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token)
    limitDailyMint(amount, token)               // ERC20 path
``` [3](#0-2) 

The identical pattern exists in `RSETHPoolV3ExternalBridge`: [4](#0-3) [5](#0-4) 

Because `dailyMintAmount` is a single slot shared by ETH and every ERC20 token (e.g., wstETH), a deposit of token A consumes capacity that token B's depositors depend on.

---

### Impact Explanation

**Medium — Temporary freezing of deposits.**

When a large ETH deposit (or a series of deposits) fills `dailyMintAmount` up to `dailyMintLimit`, every subsequent call to `deposit()` for any other supported token reverts with `DailyMintLimitExceeded` until the next calendar day resets the counter. Users holding wstETH (or any other supported LST) are locked out of the pool for up to 24 hours through no fault of their own. Their funds are not lost, but the protocol fails to deliver its promised deposit service for the remainder of the day.

---

### Likelihood Explanation

**Medium.** The pool is publicly accessible; no special role is required to call `deposit()`. Any user — including one acting adversarially — who deposits enough of one token to approach `dailyMintLimit` will inadvertently (or deliberately) block all other token depositors. On chains where ETH liquidity is deep and `dailyMintLimit` is set conservatively, a single whale transaction is sufficient to trigger the condition. The reset is automatic after 24 hours, making this repeatable every day.

---

### Recommendation

Replace the three shared scalar variables with per-token mappings:

```solidity
mapping(address token => uint256) public dailyMintAmount;
mapping(address token => uint256) public lastMintDay;
```

Update `limitDailyMint` to key on `token` (using `ETH_IDENTIFIER` for native ETH). Optionally keep a global cap as a secondary guard. This mirrors the fix recommended in the reference report: track the relevant counter per independent entity rather than sharing a single slot across all of them.

---

### Proof of Concept

1. `dailyMintLimit` is set to 1 000 rsETH.
2. Alice calls `deposit{value: X}("")` where `X` is large enough that `viewSwapRsETHAmountAndFee(X)` returns ≥ 1 000 rsETH. `dailyMintAmount` is now at or above `dailyMintLimit`.
3. Bob calls `deposit(wstETH, amount, "")` for any non-zero `amount`. Inside `limitDailyMint`, `currentDay == lastMintDay` (same block), so the counter is **not** reset. The check `dailyMintAmount + rsETHAmount > dailyMintLimit` is immediately true and the call reverts with `DailyMintLimitExceeded`.
4. Bob (and every other wstETH/LST depositor) is blocked until the next day's reset, even though the wstETH deposit is entirely independent of Alice's ETH deposit. [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L51-57)
```text
    uint256 public dailyMintLimit;

    /// @notice The amount of rsETH that was minted today
    uint256 public dailyMintAmount;

    /// @notice The last day that rsETH was minted
    uint256 public lastMintDay;
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

**File:** contracts/pools/RSETHPoolV3.sol (L246-293)
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

    /// @dev Swaps supported token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L66-73)
```text
    /// @notice THe daily minting limit for rsETH
    uint256 public dailyMintLimit;

    /// @notice The amount of rsETH that was minted today
    uint256 public dailyMintAmount;

    /// @notice The last day that rsETH was minted
    uint256 public lastMintDay;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L130-159)
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
