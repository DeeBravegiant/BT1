### Title
Missing Zero-Rate Validation in `viewSwapRsETHAmountAndFee` Allows Token Deposits to Silently Mint Zero rsETH While Consuming User Funds - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolNoWrapper` does not validate that `tokenToETHRate` is non-zero before using it in a multiplication. If a supported token's oracle returns 0, the deposit flow silently computes `rsETHAmount = 0`, takes the user's tokens, and mints 0 rsETH — permanently freezing the deposited funds in the pool.

---

### Finding Description

In `viewSwapRsETHAmountAndFee(uint256 amount, address token)`:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

If `tokenToETHRate == 0`, then `amountAfterFee * 0 / rsETHToETHrate = 0`. Solidity does not revert on `0 / x` — the result is silently 0. No error is raised.

The token `deposit` function then proceeds to:
1. Transfer the user's tokens into the contract via `safeTransferFrom`
2. Account the fee: `feeEarnedInToken[token] += fee`
3. Call `wrsETH.mint(msg.sender, 0)` — minting nothing (OpenZeppelin's `_mint` does not revert on amount 0)

The user's tokens are permanently stuck in the pool with no recovery path for the user.

The inconsistency is explicit: `viewSwapAssetToPremintedRsETH` in the same contract already guards against this exact condition:

```solidity
if (tokenToETHRate == 0) revert UnsupportedOracle();
```

but the forward deposit path does not. [1](#0-0) [2](#0-1) 

The same unguarded pattern exists in `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee`: [3](#0-2) 

And in `RSETHPoolNoWrapper.viewSwapRsETHAmountAndFee`: [4](#0-3) 

---

### Impact Explanation

User-deposited tokens are permanently frozen in the pool contract. There is no refund mechanism for depositors. The `BRIDGER_ROLE` can only move assets to L1 bridges, not return them to users. The fee portion is silently absorbed into `feeEarnedInToken`, and the principal (`amountAfterFee`) has no accounting entry — it is irrecoverable by the user. This constitutes permanent freezing of user funds. [5](#0-4) 

---

### Likelihood Explanation

Low. The `addSupportedToken` function validates that the oracle returns non-zero at the time of token addition:

```solidity
if (IOracle(oracle).getRate() == 0) {
    revert UnsupportedOracle();
}
``` [6](#0-5) 

However, after a token is added, if the oracle subsequently returns 0 — due to a stale feed, oracle contract bug, or underlying data source failure — any depositor calling `deposit(token, amount, referralId)` will trigger the vulnerability. The oracle is an external contract whose liveness is not guaranteed post-addition.

---

### Recommendation

Add a zero-rate guard in `viewSwapRsETHAmountAndFee(uint256 amount, address token)` for the token oracle rate, consistent with the check already present in `viewSwapAssetToPremintedRsETH`:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
if (tokenToETHRate == 0) revert UnsupportedOracle();
```

Apply the same fix to `RSETHPoolV3ExternalBridge` and `RSETHPoolNoWrapper`.

---

### Proof of Concept

1. A supported token's oracle begins returning 0 (e.g., oracle feed failure or stale round).
2. User calls `deposit(token, 1e18, "ref")` on `RSETHPoolV3`.
3. `limitDailyMint` modifier calls `viewSwapRsETHAmountAndFee(1e18, token)` → `tokenToETHRate = 0` → `rsETHAmount = 0`. Modifier passes (0 added to daily limit, no limit exceeded).
4. Function body: `IERC20(token).safeTransferFrom(msg.sender, address(this), 1e18)` — 1e18 tokens taken from user.
5. `viewSwapRsETHAmountAndFee(1e18, token)` called again → `rsETHAmount = 0`, `fee = 1e18 * feeBps / 10_000`.
6. `feeEarnedInToken[token] += fee` — fee silently absorbed.
7. `wrsETH.mint(msg.sender, 0)` — executes without revert, mints nothing.
8. User has lost 1e18 tokens. The `amountAfterFee` portion has no accounting entry and is irrecoverable. [7](#0-6)

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

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L392-401)
```text
        uint256 rsETHToETHrate = getRate();
        if (rsETHToETHrate == 0) revert UnsupportedOracle();

        // Rate of token in ETH
        uint256 tokenToETHRate = token == ETH_IDENTIFIER ? 1e18 : IOracle(supportedTokenOracle[token]).getRate();
        if (tokenToETHRate == 0) revert UnsupportedOracle();

        // Calculate the amount of token user will get for the amount of rsETH
        tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-555)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L433-453)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L301-312)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
