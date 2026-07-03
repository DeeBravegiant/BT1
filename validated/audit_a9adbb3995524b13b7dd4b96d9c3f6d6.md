### Title
Missing Zero-Check on Oracle-Returned Rate in `viewSwapRsETHAmountAndFee` Causes User Token Loss on L2 Pool Deposits — (`contracts/pools/RSETHPoolV3.sol`, `RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

Across all L2 pool contracts, the `viewSwapRsETHAmountAndFee(amount, token)` function uses the oracle-returned `tokenToETHRate` directly in the rsETH amount calculation without a zero-check. If the oracle returns 0, the calculation silently produces `rsETHAmount = 0`. The deposit function then takes the user's tokens and mints 0 rsETH — a direct loss of deposited funds. The reverse-swap function `viewSwapAssetToPremintedRsETH` in the same contracts explicitly guards against this with zero-checks, making the omission in the deposit path a clear inconsistency and an unguarded calculation path.

---

### Finding Description

In every L2 pool contract, the token-deposit variant of `viewSwapRsETHAmountAndFee` performs:

```solidity
uint256 rsETHToETHrate = getRate();
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

No check is performed on `tokenToETHRate` before it is used as a multiplicand. If the oracle returns 0, the expression evaluates to `0` without reverting, and `rsETHAmount = 0` is returned to the caller.

The deposit function then proceeds:

```solidity
IERC20(token).safeTransferFrom(msg.sender, address(this), amount); // tokens taken
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;   // fee = 0
wrsETH.mint(msg.sender, rsETHAmount); // mints 0
``` [2](#0-1) 

The `limitDailyMint` modifier also calls `viewSwapRsETHAmountAndFee` and receives `rsETHAmount = 0`, which passes the daily limit check (`0 + 0 > dailyMintLimit` is false). The transaction completes successfully: the user's tokens are transferred in, and 0 rsETH is minted out.

The same pattern is present in all pool variants: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

**The inconsistency is explicit**: the reverse-swap function `viewSwapAssetToPremintedRsETH` in `RSETHPoolV3` guards both rates:

```solidity
uint256 rsETHToETHrate = getRate();
if (rsETHToETHrate == 0) revert UnsupportedOracle();
uint256 tokenToETHRate = token == ETH_IDENTIFIER ? 1e18 : IOracle(supportedTokenOracle[token]).getRate();
if (tokenToETHRate == 0) revert UnsupportedOracle();
``` [7](#0-6) 

The deposit path has no equivalent guard. This is the direct analog of the external report's root cause: an intermediate value (the oracle rate) is used in a critical calculation without being constrained or validated, producing a silently incorrect result.

---

### Impact Explanation

**Critical — Direct theft of user funds.**

A user calling `deposit(token, amount, referralId)` when the token oracle returns 0 will have their full `amount` of ERC-20 tokens transferred into the pool contract and receive 0 wrsETH in return. The tokens remain in the pool, credited to no one (fee accounting records 0 fee). The user has no recourse: the transaction succeeds, the tokens are gone, and no rsETH was minted.

This affects every L2 pool contract that supports token deposits: `RSETHPoolV3`, `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`.

---

### Likelihood Explanation

**Low.** The trigger condition is the token oracle returning 0. This can occur due to:

1. A bug in the oracle contract (e.g., uninitialized state, failed data source, or a bad upgrade).
2. A newly deployed or reconfigured oracle that has not yet received a valid price feed.

The `onlySupportedToken` modifier only checks that `supportedTokenOracle[token] != address(0)` (i.e., an oracle address is set), not that the oracle returns a valid non-zero rate. An oracle address being set is a necessary but insufficient condition for correctness. [8](#0-7) 

The root cause is entirely within the LRT-rsETH codebase (missing validation). The oracle returning 0 is the trigger, not the root cause.

---

### Recommendation

Add a zero-check on `tokenToETHRate` (and `rsETHToETHrate`) in `viewSwapRsETHAmountAndFee` for all pool contracts, mirroring the guard already present in `viewSwapAssetToPremintedRsETH`:

```solidity
uint256 rsETHToETHrate = getRate();
if (rsETHToETHrate == 0) revert UnsupportedOracle();

uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
if (tokenToETHRate == 0) revert UnsupportedOracle();

rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

Apply this fix consistently across `RSETHPoolV3`, `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`.

---

### Proof of Concept

1. Admin sets a token oracle for token `T` in `RSETHPoolV3`. The oracle contract is valid (non-zero address) but returns `getRate() = 0` (e.g., uninitialized or broken).
2. User calls `RSETHPoolV3.deposit(T, 1e18, "ref")` with 1e18 of token T.
3. `limitDailyMint` modifier calls `viewSwapRsETHAmountAndFee(1e18, T)`:
   - `fee = 1e18 * feeBps / 10_000` (some small amount)
   - `amountAfterFee = 1e18 - fee`
   - `tokenToETHRate = 0` (oracle returns 0)
   - `rsETHAmount = amountAfterFee * 0 / rsETHToETHrate = 0`
4. Modifier: `dailyMintAmount + 0 > dailyMintLimit` → false → passes.
5. `IERC20(T).safeTransferFrom(user, pool, 1e18)` — tokens transferred in.
6. `viewSwapRsETHAmountAndFee` returns `(0, fee)`.
7. `wrsETH.mint(user, 0)` — 0 rsETH minted.
8. Transaction succeeds. User has lost 1e18 of token T and received nothing. [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L81-83)
```text
    modifier onlySupportedToken(address token) {
        if (supportedTokenOracle[token] == address(0)) revert UnsupportedToken();
        _;
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

**File:** contracts/pools/RSETHPoolV3.sol (L328-334)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L392-400)
```text
        uint256 rsETHToETHrate = getRate();
        if (rsETHToETHrate == 0) revert UnsupportedOracle();

        // Rate of token in ETH
        uint256 tokenToETHRate = token == ETH_IDENTIFIER ? 1e18 : IOracle(supportedTokenOracle[token]).getRate();
        if (tokenToETHRate == 0) revert UnsupportedOracle();

        // Calculate the amount of token user will get for the amount of rsETH
        tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
```

**File:** contracts/pools/RSETHPool.sol (L335-347)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L442-453)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L360-371)
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
