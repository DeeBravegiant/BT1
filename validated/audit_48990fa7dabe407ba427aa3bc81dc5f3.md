### Title
`limitDailyMint` Modifier in `RSETHPoolV3` Deposit Functions Can Be DOSed via Front-Running Near Daily Cap - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The `limitDailyMint` modifier enforces a strict daily rsETH minting cap with no partial-fill or refund logic. When `dailyMintAmount` is close to `dailyMintLimit`, an unprivileged attacker can front-run a legitimate large deposit with a smaller deposit that consumes the remaining daily capacity, causing the victim's transaction to revert with `DailyMintLimitExceeded`. The attack is cheap, repeatable every day, and requires no special privileges.

### Finding Description
The `limitDailyMint` modifier in `RSETHPoolV3` performs the following check before any deposit executes:

```solidity
// contracts/pools/RSETHPoolV3.sol L119-121
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
dailyMintAmount += rsETHAmount;
``` [1](#0-0) 

This modifier is applied to both public deposit entry points:

```solidity
// ETH deposit
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)

// Token deposit
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token)
    limitDailyMint(amount, token)
``` [2](#0-1) 

The `rsETHAmount` computed inside the modifier is derived from `viewSwapRsETHAmountAndFee`:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [3](#0-2) 

The only guard against dust deposits is `if (amount == 0) revert InvalidAmount()` — there is no `minAmountToDeposit` floor. [4](#0-3) 

**Attack path:**

1. Attacker monitors the mempool. When `dailyMintAmount` is near `dailyMintLimit` (e.g., 99 % consumed), a victim submits a large deposit that would mint `R` rsETH where `dailyMintAmount + R > dailyMintLimit`.
2. Attacker front-runs with a deposit sized to mint exactly `dailyMintLimit − dailyMintAmount − 1 wei` rsETH, pushing `dailyMintAmount` to `dailyMintLimit − 1 wei`.
3. Victim's transaction executes: `dailyMintAmount + R > dailyMintLimit` → `DailyMintLimitExceeded` revert.
4. The daily counter resets the next day (`currentDay > lastMintDay` sets `dailyMintAmount = 0`), so the attack can be repeated every day at low cost. [5](#0-4) 

The attacker retains their deposited value as `wrsETH` and can exit via the reverse-swap path, making the net cost only gas.

### Impact Explanation
Any user attempting to deposit ETH or a supported token into `RSETHPoolV3` near the daily cap can have their transaction griefed and reverted. Deposits are blocked for up to 24 hours per attack cycle. This constitutes **temporary freezing of funds** (users cannot enter the pool and their ETH/tokens are returned only because the tx reverts — but the intended deposit action is denied). Impact: **Medium**.

### Likelihood Explanation
The attack is permissionless, cheap (attacker recovers capital as `wrsETH`), and requires only mempool visibility. The daily cap is a fixed, publicly readable value (`dailyMintLimit`), making it trivial to compute the exact front-run amount. The attack is repeatable every 24 hours. Likelihood: **Medium**.

### Recommendation
Replace the hard revert with a partial-fill or cap-clamping approach: if the requested deposit would exceed the remaining daily capacity, either (a) cap the accepted amount to the remaining limit and refund the excess to the caller, or (b) revert with a clear message only when `dailyMintAmount >= dailyMintLimit` (i.e., capacity is already fully consumed), so that a deposit that fits within the remaining room always succeeds regardless of ordering.

### Proof of Concept

```
Setup:
  dailyMintLimit  = 1000e18  (rsETH)
  dailyMintAmount = 990e18   (rsETH already minted today)
  rsETH/ETH rate  ≈ 1.05

Victim tx (pending in mempool):
  deposit{value: 11 ETH}("ref")
  → rsETHAmount ≈ 10.47e18
  → 990e18 + 10.47e18 = 1000.47e18 > 1000e18  ✓ (would succeed if first)

Attacker front-runs:
  deposit{value: ~10.5 ETH}("ref")
  → rsETHAmount ≈ 10e18
  → dailyMintAmount becomes 1000e18

Victim tx executes:
  dailyMintAmount(1000e18) + rsETHAmount(10.47e18) > dailyMintLimit(1000e18)
  → revert DailyMintLimitExceeded

Result: victim's deposit is blocked for the rest of the day.
Attacker holds ~10 wrsETH and can reverse-swap to recover ETH.
``` [6](#0-5)

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

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```
