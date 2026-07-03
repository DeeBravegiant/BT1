### Title
Global Shared Daily Mint Rate Limit Can Be Exhausted by a Single Large Depositor, Temporarily Blocking All Other Users - (File: contracts/RSETH.sol)

### Summary
`RSETH.sol` enforces a global shared daily mint cap (`maxMintAmountPerDay`) via the `checkDailyMintLimit` modifier. Because the counter `currentPeriodMintedAmount` is protocol-wide and not per-user, a single whale depositing a large amount through `LRTDepositPool` can exhaust the entire 24-hour minting budget in one transaction, causing every subsequent depositor's transaction to revert with `DailyMintLimitExceeded` for up to 24 hours.

### Finding Description
`RSETH.mint()` applies the `checkDailyMintLimit` modifier before minting:

```solidity
// contracts/RSETH.sol
modifier checkDailyMintLimit(uint256 amount) {
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        periodStartTime = getCurrentPeriodStartTime();
    }
    if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
        revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
    }
    currentPeriodMintedAmount += amount;
    _;
}
```

`currentPeriodMintedAmount` is a single global accumulator shared across all callers. `LRTDepositPool.depositETH()` and `depositAsset()` both call `_mintRsETH()` which calls `RSETH.mint()`. There is no per-user sub-limit. A depositor who sends an amount of ETH/LST whose rsETH equivalent equals or exceeds `maxMintAmountPerDay - currentPeriodMintedAmount` will consume the entire remaining daily budget, and every subsequent `depositETH` or `depositAsset` call will revert until the 24-hour window resets.

The same structural issue exists in the L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV2ExternalBridge`) which each maintain their own independent `dailyMintAmount` / `dailyMintLimit` pair with the same shared-counter pattern.

### Impact Explanation
All users are blocked from depositing ETH or LSTs into the protocol for up to 24 hours after the limit is exhausted. The deposit path is the only way for users to obtain rsETH from the L1 protocol. This constitutes a **temporary freezing of the deposit functionality** — users cannot enter the protocol during the blackout window. Impact: **Medium. Temporary freezing of funds.**

### Likelihood Explanation
The `maxMintAmountPerDay` is a finite value set by the LRT manager. Any depositor whose intended deposit maps to an rsETH amount that fills the remaining daily budget can trigger this. Given that large institutional depositors or protocols routinely move hundreds or thousands of ETH in a single transaction, and the daily limit is a fixed cap, this is realistically reachable without any coordination or malicious intent. A deliberate attacker could also front-run the daily reset to immediately exhaust the new period's budget.

### Recommendation
Replace the single global accumulator with a per-depositor sub-limit, or implement a maximum single-transaction deposit cap so no single call can consume the entire daily budget. Alternatively, remove the global daily mint cap from `RSETH.mint()` and rely solely on the per-asset `depositLimitByAsset` ceiling in `LRTConfig`, which is a cumulative cap rather than a rolling time-window cap.

### Proof of Concept
1. `maxMintAmountPerDay` is set to `X` rsETH (e.g., 1000 rsETH).
2. Whale calls `LRTDepositPool.depositETH{value: V}(0, "")` where `V` is large enough that `getRsETHAmountToMint(ETH, V) >= X`.
3. `_mintRsETH(X)` → `RSETH.mint(whale, X)` → `checkDailyMintLimit(X)`: `currentPeriodMintedAmount` becomes `X`.
4. Any subsequent call to `depositETH` or `depositAsset` by any user within the same 24-hour window hits `currentPeriodMintedAmount + amount > maxMintAmountPerDay` and reverts with `DailyMintLimitExceeded`.
5. All users are locked out of deposits for up to 24 hours.

**Root cause lines:** [1](#0-0) 
**Mint function applying the shared limit:** [2](#0-1) 
**Deposit entry path in LRTDepositPool:** [3](#0-2) 
**Same pattern in L2 pool (RSETHPoolV3):** [4](#0-3)

### Citations

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }
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
