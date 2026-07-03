### Title
Missing Daily Mint Limit Enforcement in Deposit Functions - (`contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPool.sol`)

---

### Summary

`RSETHPoolNoWrapper` and `RSETHPool` expose public `deposit()` functions that transfer rsETH to depositors with no daily mint cap, while every other pool variant in the protocol (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV2`, `RSETHPoolV2ExternalBridge`) enforces a `limitDailyMint` modifier on the same entry point. The safety circuit-breaker is entirely absent from these two contracts.

---

### Finding Description

All pool variants that mint or distribute rsETH define a `limitDailyMint` modifier and apply it to their `deposit()` functions:

`RSETHPoolV3.deposit()` applies `limitDailyMint(msg.value, ETH_IDENTIFIER)`: [1](#0-0) 

`RSETHPoolV3ExternalBridge.deposit()` applies `limitDailyMint(msg.value, ETH_IDENTIFIER)`: [2](#0-1) 

The `limitDailyMint` modifier tracks `dailyMintAmount`, resets it each new day, and reverts if the cap is exceeded: [3](#0-2) 

By contrast, `RSETHPoolNoWrapper.deposit(string)` and `RSETHPoolNoWrapper.deposit(address,uint256,string)` carry only `nonReentrant` and `whenNotPaused` — no `limitDailyMint`, and no `dailyMintLimit` / `dailyMintAmount` / `lastMintDay` / `startTimestamp` state variables exist anywhere in the contract: [4](#0-3) 

`RSETHPool.deposit()` has the same omission: [5](#0-4) 

---

### Impact Explanation

The daily mint limit is the protocol's primary circuit-breaker against abnormal rsETH outflow from L2 pools. Without it, a single depositor can drain the entire pre-minted rsETH balance held by `RSETHPoolNoWrapper` or `RSETHPool` in one transaction. Once drained, all subsequent depositors receive `InsufficientBalanceInPool`-equivalent reverts until the bridger replenishes the pool, temporarily freezing the deposit service for all other users on those chains (Arbitrum, Unichain). The attacker pays fair oracle-rate value, so there is no direct theft, but the contract fails to deliver its promised rate-limited distribution guarantee.

**Impact: Low — Contract fails to deliver promised returns** (the daily distribution cap is not enforced, allowing the pool to be fully drained in a single block).

---

### Likelihood Explanation

Any unprivileged depositor with sufficient ETH or a supported LST can trigger this in a single transaction. No special role, oracle manipulation, or governance capture is required. The entry path is the public `deposit()` function, reachable by any externally-owned account.

---

### Recommendation

Add the `dailyMintLimit`, `dailyMintAmount`, `lastMintDay`, and `startTimestamp` state variables to `RSETHPoolNoWrapper` and `RSETHPool`, implement the `limitDailyMint` modifier (matching the pattern in `RSETHPoolV3`), and apply it to both `deposit()` overloads in each contract, consistent with every other pool variant in the codebase.

---

### Proof of Concept

1. Deploy `RSETHPoolNoWrapper` with a pre-minted rsETH balance of `X`.
2. Call `deposit{value: X * rate}("")` from any EOA in a single transaction.
3. The call succeeds with no daily cap check — the entire rsETH balance is transferred to the attacker.
4. All subsequent `deposit()` calls from other users revert because the pool holds no rsETH.
5. Repeat the same test against `RSETHPool.deposit()` — identical result.

Compare with `RSETHPoolV3.deposit()` under the same conditions: the `limitDailyMint` modifier reverts with `DailyMintLimitExceeded` once `dailyMintAmount + rsETHAmount > dailyMintLimit`. [6](#0-5)

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

**File:** contracts/pools/RSETHPoolV3.sol (L246-252)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-372)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-271)
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

    /// @dev Swaps token for rsETH
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
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPool.sol (L265-305)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev Swaps token for rsETH
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
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```
