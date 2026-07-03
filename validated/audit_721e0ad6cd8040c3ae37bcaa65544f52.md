### Title
Daily Mint Limit Can Be Consumed by Any Depositor to Temporarily Deny All Other Deposits - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
`RSETHPoolV3` enforces a `dailyMintLimit` on rsETH minting via the `limitDailyMint` modifier. Because this limit is a shared, monotonically-increasing counter that any unprivileged depositor can fill, a single large depositor can consume the entire day's quota in one transaction, blocking all other users from depositing for up to 24 hours. The attacker suffers no economic loss because they receive `wrsETH` tokens of equivalent value.

### Finding Description
The `limitDailyMint` modifier in `RSETHPoolV3` tracks cumulative rsETH minted per day:

```solidity
modifier limitDailyMint(uint256 amount, address token) {
    ...
    if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
        revert DailyMintLimitExceeded();
    }
    dailyMintAmount += rsETHAmount;
    _;
}
``` [1](#0-0) 

`dailyMintAmount` is only ever incremented here and reset to `0` when a new calendar day begins:

```solidity
if (currentDay > lastMintDay) {
    lastMintDay = currentDay;
    dailyMintAmount = 0;
}
``` [2](#0-1) 

There is no mechanism to decrease `dailyMintAmount` mid-day. Both public `deposit` overloads (ETH and ERC-20) apply this modifier: [3](#0-2) [4](#0-3) 

An attacker calls `deposit()` with an amount large enough to set `dailyMintAmount ≈ dailyMintLimit`. Every subsequent deposit by any user reverts with `DailyMintLimitExceeded` until midnight UTC resets the counter. The attacker receives `wrsETH` tokens worth the deposited ETH, so they bear no economic loss.

### Impact Explanation
All user deposits into `RSETHPoolV3` are blocked for up to 24 hours. Users on the affected L2 chain cannot obtain `wrsETH` during this window. This constitutes **temporary freezing of the deposit path** — a Medium-severity impact under the allowed scope ("Temporary freezing of funds").

### Likelihood Explanation
The attack requires only a single large deposit. The attacker retains full economic value via `wrsETH` tokens. No front-running, flashloan, or privileged access is needed — any whale or well-capitalised actor can execute this at the start of any day. Likelihood is **Medium**.

### Recommendation
1. **Per-depositor sub-limit**: Track `dailyMintAmountPerUser[msg.sender]` and cap individual contributions to a fraction of `dailyMintLimit`.
2. **Raise the limit significantly**: Set `dailyMintLimit` high enough that filling it in one transaction is economically prohibitive relative to the capital required.
3. **Cooldown per address**: Enforce a minimum time between large deposits from the same address.

### Proof of Concept
1. `dailyMintLimit` is set to `X` rsETH (e.g. 1 000 rsETH).
2. At the start of a new day (`currentDay > lastMintDay`), `dailyMintAmount` resets to `0`.
3. Attacker calls `RSETHPoolV3.deposit{value: V}("")` where `V` is chosen so that `viewSwapRsETHAmountAndFee(V).rsETHAmount ≥ X`.
4. `limitDailyMint` sets `dailyMintAmount = X`; attacker receives `X` wrsETH.
5. Any subsequent user calling `deposit{value: any}("")` hits:
   ```
   if (dailyMintAmount + rsETHAmount > dailyMintLimit) revert DailyMintLimitExceeded();
   ```
   and reverts.
6. The block persists until `block.timestamp` crosses into the next day, up to ~24 hours.
7. Attacker holds `wrsETH` worth the deposited ETH — zero net loss. [5](#0-4)

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
