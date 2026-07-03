Audit Report

## Title
Missing Zero-Amount Check After Integer Division Allows ETH/Token Deposits to Yield Zero rsETH — (`contracts/pools/RSETHPoolV3.sol`)

## Summary
`viewSwapRsETHAmountAndFee` computes `rsETHAmount` via integer division that truncates to zero for dust inputs. Neither `deposit` overload checks that `rsETHAmount > 0` before consuming the user's ETH or ERC-20 tokens and calling `wrsETH.mint(msg.sender, 0)`. A depositor who sends a sufficiently small amount permanently loses their funds while receiving zero `wrsETH`.

## Finding Description
`viewSwapRsETHAmountAndFee(uint256)` computes:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [1](#0-0) 

`viewSwapRsETHAmountAndFee(uint256,address)` computes:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

Solidity truncates toward zero. For the ETH path, any `amountAfterFee` where `amountAfterFee * 1e18 < rsETHToETHrate` (e.g., 1 wei when `rsETHToETHrate ≈ 1.05e18`) yields `rsETHAmount = 0`. For the token path, any `amountAfterFee * tokenToETHRate < rsETHToETHrate` yields the same.

The ETH `deposit` function only guards against `amount == 0`, then unconditionally mints: [3](#0-2) 

The token `deposit` function transfers tokens from the user *before* computing `rsETHAmount`, then unconditionally mints: [4](#0-3) 

The `limitDailyMint` modifier also calls `viewSwapRsETHAmountAndFee` and adds `rsETHAmount` to `dailyMintAmount`. When `rsETHAmount = 0`, the check `dailyMintAmount + 0 > dailyMintLimit` passes trivially, so the daily cap provides no protection either. [5](#0-4) 

By contrast, `LRTDepositPool._beforeDeposit` correctly enforces a `minRSETHAmountExpected` guard: [6](#0-5) 

The same root cause exists across all pool variants in the repository.


## Impact Explanation
A depositor who sends a dust ETH or token amount receives zero `wrsETH`. Their funds are permanently retained by the pool's balance (not credited to `feeEarnedInETH`/`feeEarnedInToken` unless `feeBps > 0`), and are unrecoverable by the user. This matches the **Low** impact tier: *"Contract fails to deliver promised returns, but doesn't lose value."* [7](#0-6) 

## Likelihood Explanation
Any unprivileged external caller can trigger this by calling the public `deposit` functions with a sufficiently small amount. No special role, precondition, or coordination is required. It can occur accidentally (UI rounding, dust from prior transactions) or deliberately. For ETH, the threshold is 1 wei; for tokens with low ETH-denominated rates (e.g., `tokenToETHRate ≈ 4e14`), the threshold is approximately `rsETHToETHrate / tokenToETHRate ≈ 2625` token units. [8](#0-7) 

## Recommendation
1. Add `require(rsETHAmount > 0, "Zero rsETH output")` (or a custom error revert) in both `deposit` overloads immediately after calling `viewSwapRsETHAmountAndFee`, before any state changes or token transfers.
2. Optionally expose a `minRsETHAmountExpected` parameter (mirroring `LRTDepositPool.depositETH`) to give callers explicit slippage protection. [9](#0-8) 

## Proof of Concept
**ETH path:**
1. `rsETHToETHrate = getRate()` returns `1.05e18`.
2. Call `deposit{value: 1}("")` (1 wei).
3. `viewSwapRsETHAmountAndFee(1)`: `fee = 0`, `amountAfterFee = 1`, `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`.
4. `feeEarnedInETH += 0`.
5. `wrsETH.mint(msg.sender, 0)` — no revert, 0 tokens minted.
6. Pool balance increases by 1 wei; user receives nothing.

**Token path:**
1. Token oracle returns `tokenToETHRate = 4e14`; `rsETHToETHrate = 1.05e18`.
2. Approve pool for 2000 token units; call `deposit(token, 2000, "")`.
3. `safeTransferFrom` takes 2000 units from user.
4. `viewSwapRsETHAmountAndFee(2000, token)`: `rsETHAmount = 2000 * 4e14 / 1.05e18 = 0`.
5. `wrsETH.mint(user, 0)` — no revert, 0 tokens minted.
6. User loses 2000 token units permanently. [10](#0-9)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-123)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
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

**File:** contracts/pools/RSETHPoolV3.sol (L307-307)
```text
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L334-334)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
