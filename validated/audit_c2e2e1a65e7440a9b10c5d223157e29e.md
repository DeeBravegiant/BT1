### Title
Attacker Can Exhaust the Global `dailyMintLimit` to Temporarily Block All Deposits - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The `limitDailyMint` modifier in `RSETHPoolV3` (and its variants) maintains a single shared `dailyMintAmount` counter. Any unprivileged depositor can consume the entire daily quota in one transaction, blocking all other users from depositing for up to 24 hours at the cost of only a protocol fee.

### Finding Description
The `limitDailyMint` modifier accumulates a global `dailyMintAmount` counter that is shared across all depositors. When a deposit is made, the modifier computes the rsETH equivalent of the deposit, checks that `dailyMintAmount + rsETHAmount <= dailyMintLimit`, and then unconditionally increments `dailyMintAmount`:

```solidity
// contracts/pools/RSETHPoolV3.sol  lines 118-123
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
dailyMintAmount += rsETHAmount;
```

There is no per-user sub-limit, no whitelist bypass, and no mechanism to reclaim the consumed quota. An attacker simply calls `deposit()` with an amount large enough to push `dailyMintAmount` to `dailyMintLimit`. Because the attacker receives `wrsETH` in return, the only real cost is the `feeBps` fee. After the attack, every subsequent `deposit()` call by any user reverts with `DailyMintLimitExceeded` until the next calendar day resets the counter.

The same pattern is replicated verbatim in:
- `contracts/pools/RSETHPoolV3ExternalBridge.sol` (lines 130–158)
- `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol` (lines 110–136)
- `contracts/pools/RSETHPoolV2.sol` (lines 72–93)
- `contracts/pools/RSETHPoolV2ExternalBridge.sol` (lines 104–125)

### Impact Explanation
All L2 pool deposits are blocked for up to 24 hours. Users who need to enter a position (e.g., to hedge a time-sensitive trade, capture a yield opportunity, or respond to market conditions) are denied access. The protocol remains in this degraded state until the next day's reset, with no on-chain remedy available to users. This constitutes **temporary freezing of funds** (user funds are not lost, but access to the protocol is denied for a bounded period).

**Severity: Medium** — Temporary freezing of funds.

### Likelihood Explanation
The attack is cheap: the attacker pays only the `feeBps` fee (e.g., 0.1–0.5% of the deposited amount) and receives `wrsETH` in return, which can be immediately redeemed or held. No special role, flash loan, or oracle manipulation is required. The attack can be repeated every 24 hours. Any actor with sufficient capital (equal to `dailyMintLimit` worth of ETH/LST) can execute it permissionlessly.

### Recommendation
Introduce a per-address sub-limit or a deposit cap per transaction so that no single depositor can consume the entire daily quota. Alternatively, consider removing the global daily mint limit from the L2 pool contracts entirely and relying on the L1 deposit limit (`lrtConfig.depositLimitByAsset`) as the authoritative cap, since the L2 pool's daily limit provides only a weak rate-limit that is trivially bypassable.

### Proof of Concept

1. `dailyMintLimit` is set to `X` rsETH-equivalent (e.g., 100 ETH worth).
2. Attacker calls `RSETHPoolV3.deposit{value: Y}("")` where `Y` is chosen so that `viewSwapRsETHAmountAndFee(Y).rsETHAmount == X`.
3. Inside `limitDailyMint`:
   - `currentDay > lastMintDay` → `dailyMintAmount` resets to 0 (first deposit of the day).
   - `0 + X <= X` → check passes.
   - `dailyMintAmount = X`.
4. Attacker receives `X` wrsETH.
5. Any subsequent call by a legitimate user with any non-zero amount reverts: `dailyMintAmount + rsETHAmount > dailyMintLimit`.
6. State persists until `getCurrentDay()` increments (up to 24 hours later).

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-124)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L110-136)
```text
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
