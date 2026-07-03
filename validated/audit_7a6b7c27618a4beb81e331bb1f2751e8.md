### Title
Any depositor can exhaust the shared daily mint limit, temporarily blocking all other users from minting wrsETH - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The `limitDailyMint` modifier in every L2 pool variant tracks a single shared `dailyMintAmount` counter against a fixed `dailyMintLimit`. Any unprivileged depositor can exhaust the entire daily quota in one transaction, causing every subsequent deposit call to revert with `DailyMintLimitExceeded` for up to 24 hours. The attack is repeatable every day and the attacker suffers no net loss because they receive wrsETH in return.

### Finding Description
All L2 pool contracts (`RSETHPoolV2`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) share the same pattern:

```
uint256 public dailyMintLimit;
uint256 public dailyMintAmount;
uint256 public lastMintDay;
```

The `limitDailyMint` modifier resets `dailyMintAmount` to zero when a new day begins, then accumulates the minted amount and reverts if the limit is exceeded:

```solidity
if (currentDay > lastMintDay) {
    lastMintDay = currentDay;
    dailyMintAmount = 0;
}
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
dailyMintAmount += rsETHAmount;
```

There is no per-user cap, no minimum deposit size beyond `amount > 0`, and no mechanism to prevent a single depositor from consuming the entire quota. The `deposit` and `swapETHToRsETH` entry points are fully public and permissionless.

The `swapAssetToPremintedRsETH` reverse-swap function is restricted to `OPERATOR_ROLE` (V3 pools) or `onlyOperatorOrWhitelisted` (V2ExternalBridge), so the attacker cannot immediately recycle their capital. However, the attacker simply holds wrsETH — a yield-bearing token — so there is no economic loss; the only cost is the protocol fee (`feeBps`), which is a small fraction of the deposit.

### Impact Explanation
Once the daily limit is exhausted, every other user's deposit call reverts for up to 24 hours. This is a **temporary freezing of user access to the deposit function** across all L2 pools that share this pattern. The attack is repeatable every day at the cost of only the swap fee, making it a sustained denial-of-service on the L2 deposit path. Impact: **Medium — temporary freezing of funds** (users cannot deposit ETH/LSTs into the protocol for up to 24 hours per attack cycle).

### Likelihood Explanation
The attack requires capital equal to `dailyMintLimit` worth of ETH/LSTs, but the attacker receives wrsETH in return (no net loss, only opportunity cost and the fee). Any well-capitalised actor — including a competitor protocol, a large holder, or a MEV bot — can execute this. The entry path is fully permissionless. Likelihood: **Medium**.

### Recommendation
1. Introduce a per-address deposit cap within each 24-hour window so no single depositor can exhaust the global quota.
2. Alternatively, implement a per-transaction cap as a fraction of `dailyMintLimit` (e.g., no single deposit may exceed 10% of the daily limit).
3. Consider a cooldown between large deposits from the same address.

### Proof of Concept
1. Observe `dailyMintLimit` (e.g., 1 000 wrsETH) and `remainingDailyMintLimit()` (e.g., 1 000 wrsETH at the start of a day).
2. Call `deposit{value: X}(referralId)` on `RSETHPoolV3` with `X` large enough that `viewSwapRsETHAmountAndFee(X).rsETHAmount == dailyMintLimit`. The modifier sets `dailyMintAmount = dailyMintLimit`.
3. Any subsequent call by any other user to `deposit` or `swapETHToRsETH` reverts with `DailyMintLimitExceeded` until the next calendar day.
4. At the start of the next day, repeat step 2. The attacker holds wrsETH (appreciating asset) and pays only `feeBps` per cycle.

The root cause is in the shared counter with no per-user guard: [1](#0-0) 

The same pattern is present in every pool variant: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L118-124)
```text
        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV2.sol (L87-93)
```text
        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L119-125)
```text
        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L152-158)
```text
        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L130-136)
```text
        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```
