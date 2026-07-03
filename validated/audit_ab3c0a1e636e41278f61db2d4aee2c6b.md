### Title
Daily Mint Limit Not Reduced When wrsETH Is Reverse-Swapped — (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`, `contracts/pools/RSETHPoolV2ExternalBridge.sol`)

---

### Summary

The `dailyMintAmount` counter is incremented on every deposit but is never decremented when wrsETH is reverse-swapped (burned) via `swapAssetToPremintedRsETH`. This is the direct analog of the TAU `_decreaseCurrentMinted` bug: in TAU the vault's mint limit was never freed after burns; here the pool's daily mint capacity is never freed after reverse swaps, causing legitimate depositors to be temporarily blocked even though the net outstanding wrsETH is lower than the limit.

---

### Finding Description

Every deposit path in the affected pool contracts passes through the `limitDailyMint` modifier, which accumulates `dailyMintAmount`:

```solidity
// RSETHPoolV3.sol lines 119-124
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
dailyMintAmount += rsETHAmount;
```

The reverse operation, `swapAssetToPremintedRsETH`, transfers rsETH from the caller into the wrapper contract (effectively removing it from circulation) and returns ETH or tokens to the caller. This is the functional inverse of `deposit`. However, `swapAssetToPremintedRsETH` contains no corresponding decrement of `dailyMintAmount`:

```solidity
// RSETHPoolV3.sol lines 414-450
function swapAssetToPremintedRsETH(
    address rsETH,
    address token,
    uint256 rsETHAmount
) external nonReentrant onlySupportedTokenOrEth(token) onlyRole(OPERATOR_ROLE) {
    // ... transfers rsETH to wrapper, sends token/ETH back ...
    // dailyMintAmount is never decreased here
    emit ReverseSwapOccurred(msg.sender, rsETH, token, rsETHAmount, tokenAmount);
}
```

The same omission is present identically in `RSETHPoolV3ExternalBridge.swapAssetToPremintedRsETH` (lines 578–614), `RSETHPoolV3WithNativeChainBridge.swapAssetToPremintedRsETH` (lines 448–484), and `RSETHPoolV2ExternalBridge.swapAssetToPremintedRsETH` (lines 418–446).

---

### Impact Explanation

After an operator performs one or more reverse swaps, the `dailyMintAmount` remains at its peak value for the day. The actual net outstanding wrsETH is lower than `dailyMintAmount` reflects, but the limit check uses the stale inflated counter. Depositors who attempt to deposit after a reverse swap are rejected with `DailyMintLimitExceeded` even though the pool has capacity. The block persists until midnight UTC (relative to `startTimestamp`) when `dailyMintAmount` resets to zero. This constitutes a **temporary freezing of user deposits** for up to 24 hours.

**Impact: Medium — Temporary freezing of funds (deposits blocked for up to one full day).**

---

### Likelihood Explanation

`swapAssetToPremintedRsETH` is an intended operational function used to rebalance the pool (e.g., when bridged rsETH arrives on L2 and needs to be swapped for the pool's ETH/LST inventory). It is expected to be called regularly as part of normal protocol operations. Every such call silently consumes daily mint capacity that is never returned. On a day with high deposit activity followed by operator reverse swaps, the limit will be exhausted and all further deposits will revert until the next day.

In `RSETHPoolV2ExternalBridge`, the function is accessible to `WHITELISTED_USER_ROLE` holders in addition to operators, broadening the set of callers who can trigger the capacity drain.

**Likelihood: Medium — Normal operator activity triggers the issue without any malicious intent.**

---

### Recommendation

Decrement `dailyMintAmount` inside `swapAssetToPremintedRsETH` by the equivalent wrsETH amount being reverse-swapped, mirroring the increment performed in `limitDailyMint`. Apply the same fix to all four affected pool contracts. A helper should compute the wrsETH-equivalent of the returned asset amount (using the same oracle rate used during minting) and subtract it from `dailyMintAmount`, flooring at zero to avoid underflow.

---

### Proof of Concept

1. `dailyMintLimit` is set to 100 wrsETH. `dailyMintAmount = 0`.
2. Users deposit ETH totalling 100 wrsETH minted. `dailyMintAmount = 100`. Limit reached.
3. Operator calls `swapAssetToPremintedRsETH` with `rsETHAmount = 50`. The 50 wrsETH is transferred to the wrapper; 50 ETH-equivalent is returned to the operator. Net outstanding wrsETH is now 50.
4. A new depositor calls `deposit`. The `limitDailyMint` modifier checks `dailyMintAmount + rsETHAmount > dailyMintLimit` → `100 + any > 100` → **reverts with `DailyMintLimitExceeded`**.
5. The depositor is blocked for up to 24 hours despite the pool having 50 wrsETH of real capacity available.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-124)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3.sol (L414-450)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        address token,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlySupportedTokenOrEth(token)
        onlyRole(OPERATOR_ROLE)
    {
        UtilLib.checkNonZeroAddress(rsETH);

        IRsETHTokenWrapper wrapper = IRsETHTokenWrapper(address(wrsETH));
        IERC20 tokenContract = IERC20(token);

        if (!wrapper.allowedTokens(rsETH)) revert TokenNotAllowedInWrapper();
        if (rsETHAmount == 0) revert InvalidAmount();
        if (rsETHAmount > wrapper.maxAmountToDepositBridgerAsset(rsETH)) revert ExceedsMaxAmountToDepositInWrapper();

        // Get the amount of token to transfer to the user for the given amount of rsETH provided
        uint256 tokenAmount = viewSwapAssetToPremintedRsETH(token, rsETHAmount);

        // Transfer rsETH from sender to the wrapper
        IERC20(rsETH).safeTransferFrom(msg.sender, address(wrapper), rsETHAmount);

        // Transfer the token from the pool to the sender
        if (token == ETH_IDENTIFIER) {
            if (getETHBalanceMinusFees() < tokenAmount) revert InsufficientETHBalanceForReverseSwap();
            (bool success,) = payable(msg.sender).call{ value: tokenAmount }("");
            if (!success) revert TransferFailed();
        } else {
            if (getTokenBalanceMinusFees(token) < tokenAmount) revert InsufficientAssetBalanceForReverseSwap();
            tokenContract.safeTransfer(msg.sender, tokenAmount);
        }

        emit ReverseSwapOccurred(msg.sender, rsETH, token, rsETHAmount, tokenAmount);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L153-158)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L578-614)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        address token,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlySupportedTokenOrEth(token)
        onlyRole(OPERATOR_ROLE)
    {
        UtilLib.checkNonZeroAddress(rsETH);

        IRsETHTokenWrapper wrapper = IRsETHTokenWrapper(address(wrsETH));
        IERC20 tokenContract = IERC20(token);

        if (!wrapper.allowedTokens(rsETH)) revert TokenNotAllowedInWrapper();
        if (rsETHAmount == 0) revert InvalidAmount();
        if (rsETHAmount > wrapper.maxAmountToDepositBridgerAsset(rsETH)) revert ExceedsMaxAmountToDepositInWrapper();

        // Get the amount of token to transfer to the user for the given amount of rsETH provided
        uint256 tokenAmount = viewSwapAssetToPremintedRsETH(token, rsETHAmount);

        // Transfer rsETH from sender to the wrapper
        IERC20(rsETH).safeTransferFrom(msg.sender, address(wrapper), rsETHAmount);

        // Transfer the token from the pool to the sender
        if (token == ETH_IDENTIFIER) {
            if (getETHBalanceMinusFees() < tokenAmount) revert InsufficientETHBalanceForReverseSwap();
            (bool success,) = payable(msg.sender).call{ value: tokenAmount }("");
            if (!success) revert TransferFailed();
        } else {
            if (getTokenBalanceMinusFees(token) < tokenAmount) revert InsufficientAssetBalanceForReverseSwap();
            tokenContract.safeTransfer(msg.sender, tokenAmount);
        }

        emit ReverseSwapOccurred(msg.sender, rsETH, token, rsETHAmount, tokenAmount);
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L120-125)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L418-446)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlyOperatorOrWhitelisted(msg.sender)
    {
        UtilLib.checkNonZeroAddress(rsETH);

        IRsETHTokenWrapper wrapper = IRsETHTokenWrapper(address(wrsETH));

        if (!wrapper.allowedTokens(rsETH)) revert TokenNotAllowedInWrapper();
        if (rsETHAmount == 0) revert InvalidAmount();
        if (rsETHAmount > wrapper.maxAmountToDepositBridgerAsset(rsETH)) revert ExceedsMaxAmountToDepositInWrapper();

        // Get the amount of ETH to transfer to the user for the given amount of rsETH provided
        uint256 ethAmount = viewSwapAssetToPremintedRsETH(rsETHAmount);

        // Transfer rsETH from sender to the wrapper
        IERC20(rsETH).safeTransferFrom(msg.sender, address(wrapper), rsETHAmount);

        // Transfer the ETH from the pool to the sender
        if (getETHBalanceMinusFees() < ethAmount) revert InsufficientETHBalanceForReverseSwap();
        (bool success,) = payable(msg.sender).call{ value: ethAmount }("");
        if (!success) revert TransferFailed();

        emit ReverseSwapOccurred(msg.sender, rsETH, rsETHAmount, ethAmount);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L131-136)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L448-484)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        address token,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlySupportedTokenOrEth(token)
        onlyRole(OPERATOR_ROLE)
    {
        UtilLib.checkNonZeroAddress(rsETH);

        IRsETHTokenWrapper wrapper = IRsETHTokenWrapper(address(wrsETH));
        IERC20 tokenContract = IERC20(token);

        if (!wrapper.allowedTokens(rsETH)) revert TokenNotAllowedInWrapper();
        if (rsETHAmount == 0) revert InvalidAmount();
        if (rsETHAmount > wrapper.maxAmountToDepositBridgerAsset(rsETH)) revert ExceedsMaxAmountToDepositInWrapper();

        // Get the amount of token to transfer to the user for the given amount of rsETH provided
        uint256 tokenAmount = viewSwapAssetToPremintedRsETH(token, rsETHAmount);

        // Transfer rsETH from sender to the wrapper
        IERC20(rsETH).safeTransferFrom(msg.sender, address(wrapper), rsETHAmount);

        // Transfer the token from the pool to the sender
        if (token == ETH_IDENTIFIER) {
            if (getETHBalanceMinusFees() < tokenAmount) revert InsufficientETHBalanceForReverseSwap();
            (bool success,) = payable(msg.sender).call{ value: tokenAmount }("");
            if (!success) revert TransferFailed();
        } else {
            if (getTokenBalanceMinusFees(token) < tokenAmount) revert InsufficientAssetBalanceForReverseSwap();
            tokenContract.safeTransfer(msg.sender, tokenAmount);
        }

        emit ReverseSwapOccurred(msg.sender, rsETH, token, rsETHAmount, tokenAmount);
    }
```
