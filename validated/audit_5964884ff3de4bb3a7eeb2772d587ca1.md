### Title
Division by Zero in `viewSwapRsETHAmountAndFee` When Oracle Returns Zero — (File: `contracts/pools/RSETHPoolV3.sol`)

---

### Summary

The `viewSwapRsETHAmountAndFee` functions in `RSETHPoolV3.sol` (and sibling pool contracts) divide by `rsETHToETHrate` without a zero guard. If the oracle returns `0`, every call to `deposit()` reverts with a division-by-zero panic, bricking the user-facing deposit path. This is the Solidity analog of OMP-16, where `PubKeyToBytes64()` panics on a nil input because the return value of an external call is used directly in an unsafe operation without a zero/nil check.

---

### Finding Description

`viewSwapRsETHAmountAndFee` (ETH variant) computes:

```solidity
// contracts/pools/RSETHPoolV3.sol  line 304-307
uint256 rsETHToETHrate = getRate();          // external oracle call
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // no zero guard
``` [1](#0-0) 

The token-deposit variant has the same pattern:

```solidity
// contracts/pools/RSETHPoolV3.sol  line 328-334
uint256 rsETHToETHrate = getRate();
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

This function is invoked inside the `limitDailyMint` modifier, which is applied to both `deposit()` overloads:

```solidity
// contracts/pools/RSETHPoolV3.sol  line 96-125
modifier limitDailyMint(uint256 amount, address token) {
    ...
    if (token == ETH_IDENTIFIER) {
        (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
    } else {
        (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
    }
    ...
}
``` [3](#0-2) 

The same unguarded division exists in `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPool.sol`, and `RSETHPoolV2ExternalBridge.sol`. [4](#0-3) 

By contrast, the reverse-swap path (`viewSwapAssetToPremintedRsETH`) **does** include a zero guard, demonstrating the inconsistency:

```solidity
// contracts/pools/RSETHPoolV3.sol  line 392-393
uint256 rsETHToETHrate = getRate();
if (rsETHToETHrate == 0) revert UnsupportedOracle();
``` [5](#0-4) 

The oracle is validated only at token-addition time:

```solidity
// contracts/pools/RSETHPoolV3.sol  line 548-549
if (IOracle(oracle).getRate() == 0) {
    revert UnsupportedOracle();
}
``` [6](#0-5) 

There is no runtime guard preventing the oracle from returning `0` after it has been registered.

---

### Impact Explanation

If the oracle returns `0` at runtime (due to an oracle contract bug, a silent upgrade, or a misconfigured replacement), every call to `deposit()` reverts with a Solidity arithmetic panic (division by zero). No user can mint `wrsETH` on any affected L2 pool. Existing pool balances are unaffected, but the contract fails to deliver its core promised service — accepting deposits and minting liquid restaking tokens.

**Impact: Low** — Contract fails to deliver promised returns, but does not lose value.

---

### Likelihood Explanation

The oracle is set by a privileged role and validated at setup. However, oracle contracts can be upgraded or replaced, and a new implementation could transiently return `0` (e.g., during an upgrade window, a Chainlink feed returning a stale/zero answer before the wrapper reverts, or a custom oracle with a latent bug). The check at `addSupportedToken` time provides no runtime protection. Likelihood is **Low** but non-zero.

---

### Recommendation

Add a zero guard in `viewSwapRsETHAmountAndFee` before the division, consistent with the guard already present in `viewSwapAssetToPremintedRsETH`:

```solidity
uint256 rsETHToETHrate = getRate();
if (rsETHToETHrate == 0) revert UnsupportedOracle();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Apply the same fix to all pool variants (`RSETHPoolV3ExternalBridge`, `RSETHPoolNoWrapper`, `RSETHPool`, `RSETHPoolV2ExternalBridge`).

---

### Proof of Concept

1. Deploy `RSETHPoolV3` with a mock oracle that initially returns a valid rate (e.g., `1.05e18`).
2. Upgrade or replace the oracle so that `getRate()` returns `0`.
3. Call `deposit{value: 1 ether}("ref")`.
4. The `limitDailyMint` modifier calls `viewSwapRsETHAmountAndFee(1 ether)`.
5. `rsETHToETHrate = getRate()` returns `0`.
6. `rsETHAmount = amountAfterFee * 1e18 / 0` → Solidity 0.8 arithmetic panic, transaction reverts.
7. All subsequent deposits revert until the oracle is fixed; no user can mint `wrsETH`.

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

**File:** contracts/pools/RSETHPoolV3.sol (L303-307)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L328-334)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L391-397)
```text
        // Rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();
        if (rsETHToETHrate == 0) revert UnsupportedOracle();

        // Rate of token in ETH
        uint256 tokenToETHRate = token == ETH_IDENTIFIER ? 1e18 : IOracle(supportedTokenOracle[token]).getRate();
        if (tokenToETHRate == 0) revert UnsupportedOracle();
```

**File:** contracts/pools/RSETHPoolV3.sol (L547-550)
```text
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L130-158)
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
```
