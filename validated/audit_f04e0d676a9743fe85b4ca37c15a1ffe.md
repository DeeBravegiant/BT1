### Title
Daily Mint Limit Exhaustion DoS — Any Depositor Can Block All Pool Deposits for 24 Hours - (File: contracts/pools/RSETHPoolV2.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

The `limitDailyMint` modifier in all L2 pool contracts enforces a shared `dailyMintLimit` on rsETH minting. Because the limit is global and any single depositor can consume the entire remaining quota in one transaction, a malicious depositor can exhaust the daily mint cap immediately after it resets, blocking every other user from depositing for up to 24 hours. The attacker's only cost is the deposit fee (`feeBps`); their principal is preserved as wrsETH.

---

### Finding Description

Every L2 pool contract (`RSETHPoolV2`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) gates its public `deposit()` function behind the `limitDailyMint` modifier. The modifier maintains two storage variables — `dailyMintAmount` (cumulative rsETH minted today) and `dailyMintLimit` (the cap) — and resets `dailyMintAmount` to zero once a new calendar day begins relative to `startTimestamp`.

```solidity
// RSETHPoolV2.sol (identical pattern in all pool variants)
modifier limitDailyMint(uint256 amount) {
    (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
    uint256 currentDay = getCurrentDay();
    if (currentDay > lastMintDay) {
        lastMintDay = currentDay;
        dailyMintAmount = 0;          // reset once per day
    }
    if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
        revert DailyMintLimitExceeded();
    }
    dailyMintAmount += rsETHAmount;
    _;
}
```

There is no per-user sub-limit, no minimum deposit size enforced by the rate-limit logic, and no mechanism to prevent a single address from consuming the entire `dailyMintLimit` in one call. The attacker simply calls `deposit()` with enough ETH (or supported token) to push `dailyMintAmount` to `dailyMintLimit`. All subsequent `deposit()` calls from any address revert with `DailyMintLimitExceeded` until the next day's reset.

The attacker is not penalized beyond the deposit fee. They receive wrsETH proportional to their deposit (minus `feeBps`), which they hold or sell. The attack is therefore repeatable every 24 hours at a cost of only `feeBps` of the capital deployed.

---

### Impact Explanation

**Medium — Temporary freezing of funds (deposit path).**

All legitimate users are locked out of the pool's `deposit()` entry point for up to 24 hours per attack cycle. Because the L2 pools are the primary on-ramp for users to obtain wrsETH on L2 chains, this effectively freezes the deposit functionality of the protocol for the duration of the window. Users who need to enter the protocol (e.g., to hedge, to participate in yield strategies, or to bridge rsETH) are unable to do so. The freeze is temporary (resets each day) but can be sustained indefinitely by repeating the attack each day.

---

### Likelihood Explanation

**Medium.**

The attacker must hold capital equal to the `dailyMintLimit` (denominated in ETH or a supported token). On L2 chains where gas is cheap, the transaction cost is negligible. The only recurring cost is `feeBps` of the deposited amount per day. If `feeBps` is low (e.g., 10–50 bps), the annualized cost of a sustained DoS is 3.65%–18.25% of the capital deployed — a realistic cost for a motivated adversary (e.g., a competitor protocol). The attacker retains the wrsETH, so the net economic loss is only the fee, not the principal.

---

### Recommendation

1. **Per-address sub-limits**: Introduce a per-depositor cap within each daily window so no single address can consume the entire `dailyMintLimit`.
2. **Minimum deposit fee floor**: Ensure `feeBps` is set to a value that makes exhausting the daily limit economically unattractive.
3. **Shorter reset windows**: Consider finer-grained windows (e.g., hourly) so the maximum DoS duration is reduced.
4. **Off-chain monitoring**: Alert on transactions that consume a large fraction of the remaining daily limit in a single call, enabling rapid admin response (e.g., temporarily raising the limit or pausing the attacker's address).

---

### Proof of Concept

**Setup**: `dailyMintLimit = 1000 rsETH`, `feeBps = 10` (0.1%), rsETH/ETH rate = 1.0 (for simplicity).

1. At the start of a new day (`currentDay > lastMintDay`), `dailyMintAmount` resets to 0.
2. Eve calls `RSETHPoolV2.deposit{value: ~1001 ETH}("ref")`.
   - `limitDailyMint` computes `rsETHAmount ≈ 1000 rsETH` (after fee).
   - `dailyMintAmount` becomes `1000`, equal to `dailyMintLimit`.
3. Eve receives `~1000 wrsETH`. Her only cost is `~1 ETH` in fees.
4. Any subsequent call to `deposit()` by any user reverts with `DailyMintLimitExceeded` until the next day.
5. Eve repeats step 2 each day, sustaining the DoS indefinitely.

**Affected entry points** (identical `limitDailyMint` pattern):

- `RSETHPoolV2.deposit()` [1](#0-0) 
- `RSETHPoolV2ExternalBridge.deposit()` [2](#0-1) 
- `RSETHPoolV3.deposit()` (ETH and token variants) [3](#0-2) 
- `RSETHPoolV3ExternalBridge.deposit()` [4](#0-3) 
- `RSETHPoolV3WithNativeChainBridge.deposit()` [5](#0-4) 

**Root cause — shared global counter with no per-user cap**: [6](#0-5) 

The `dailyMintAmount` accumulator is global; a single depositor can drive it from 0 to `dailyMintLimit` in one transaction, and the modifier has no mechanism to prevent this. [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L72-94)
```text
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
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

**File:** contracts/pools/RSETHPoolV2.sol (L207-219)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-301)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L108-137)
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
