### Title
Missing Daily Mint Limit Allows Single-Transaction Pool Drainage - (File: contracts/pools/RSETHPoolNoWrapper.sol)

### Summary
`RSETHPoolNoWrapper` is missing the daily mint/distribution limit check that every other L2 pool contract enforces. Any unprivileged depositor can drain the pool's entire pre-minted rsETH balance in a single transaction, permanently preventing subsequent depositors from receiving rsETH through this pool until it is manually refilled.

### Finding Description
Every other L2 pool contract in the protocol — `RSETHPoolV2`, `RSETHPoolV3`, and `RSETHPoolV3ExternalBridge` — enforces a `limitDailyMint` modifier on their `deposit` functions. This modifier tracks how much rsETH has been distributed in the current 24-hour window and reverts if the daily cap would be exceeded.

`RSETHPoolNoWrapper` contains no such limit. Both of its public `deposit` functions are callable by any user with no cap on how much rsETH can be taken from the pool in a single block or day: [1](#0-0) [2](#0-1) 

Compare to `RSETHPoolV3`, which applies `limitDailyMint` on both deposit paths: [3](#0-2) [4](#0-3) 

The `limitDailyMint` modifier in `RSETHPoolV3` tracks `dailyMintAmount` and enforces `dailyMintLimit`: [5](#0-4) 

`RSETHPoolNoWrapper` declares none of these state variables and has no equivalent guard.

### Impact Explanation
An attacker can call `deposit(string)` or `deposit(address,uint256,string)` with the maximum ETH or token amount the pool holds, receiving the pool's entire rsETH balance in one transaction. After this, the pool holds zero rsETH and every subsequent legitimate depositor's call reverts on the `safeTransfer`, making the pool non-functional until an operator manually refills it. This matches the **Low** impact tier: the contract fails to deliver its promised service (rsETH in exchange for ETH/LST) to subsequent users, but no value is destroyed — the attacker paid the oracle-quoted rate.

### Likelihood Explanation
The entry path is fully permissionless: any EOA or contract can call `deposit` on `RSETHPoolNoWrapper` with no role requirement. The pool is deployed on chains where it is the primary deposit venue (e.g., Arbitrum, Unichain). A single well-funded transaction is sufficient to drain it. Likelihood is high given the zero barrier to entry.

### Recommendation
Add the same daily distribution limit that `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` use. Introduce `dailyMintLimit`, `dailyMintAmount`, `lastMintDay`, and `startTimestamp` state variables, and apply a `limitDailyMint` modifier (or equivalent internal check) to both `deposit` overloads in `RSETHPoolNoWrapper`, mirroring the pattern at: [5](#0-4) 

### Proof of Concept
1. Attacker observes `RSETHPoolNoWrapper` holds `X` rsETH (e.g., 1 000 rsETH).
2. Attacker calls `deposit{value: X * getRate() / 1e18}("")` — a single ETH deposit sized to consume the entire pool balance.
3. `viewSwapRsETHAmountAndFee` returns `rsETHAmount ≈ X`; no daily-limit check exists to block this.
4. `rsETH.safeTransfer(msg.sender, X)` succeeds; pool rsETH balance is now 0.
5. Every subsequent user calling `deposit` receives a revert from the zero-balance `safeTransfer`, making the pool non-functional. [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-243)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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
