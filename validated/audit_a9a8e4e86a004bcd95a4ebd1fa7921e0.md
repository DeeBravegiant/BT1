### Title
Daily Mint Limit Not Restored on Reverse Swap - (File: `contracts/pools/RSETHPoolV2ExternalBridge.sol`)

### Summary
In `RSETHPoolV2ExternalBridge`, when a user deposits ETH the `dailyMintAmount` counter is incremented by the `limitDailyMint` modifier. However, when a whitelisted user performs the reverse swap via `swapAssetToPremintedRsETH()`, the `dailyMintAmount` is never decremented. A holder of `WHITELISTED_USER_ROLE` can repeatedly deposit and reverse-swap within the same day to exhaust the daily mint limit, blocking all other depositors for up to 24 hours.

### Finding Description

**Deposit path — cap consumed:**

The `limitDailyMint` modifier unconditionally increments `dailyMintAmount` before the function body executes: [1](#0-0) 

```solidity
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
dailyMintAmount += rsETHAmount;
_;
```

This modifier is applied to `deposit()`: [2](#0-1) 

**Reverse-swap path — cap NOT restored:**

`swapAssetToPremintedRsETH()` is the functional inverse of `deposit()`. It accepts rsETH from the caller and returns ETH from the pool. It is accessible to `WHITELISTED_USER_ROLE` holders: [3](#0-2) 

Critically, this function contains **no decrement of `dailyMintAmount`**. After the reverse swap, the daily mint capacity consumed by the original deposit is permanently lost for the remainder of that 24-hour window.

The `onlyOperatorOrWhitelisted` modifier that gates this function: [4](#0-3) 

The `WHITELISTED_USER_ROLE` is a distinct role from `OPERATOR_ROLE` and is intended to be grantable to external users: [5](#0-4) 

The same structural gap exists in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge`, but those contracts restrict `swapAssetToPremintedRsETH()` to `OPERATOR_ROLE` only, making the attack path require a higher-privilege role.

### Impact Explanation

A malicious `WHITELISTED_USER_ROLE` holder can exhaust the entire `dailyMintLimit` within a single transaction sequence, causing `DailyMintLimitExceeded` to revert for every subsequent depositor until the next day's reset. This constitutes a **temporary freeze of the deposit function** for all users for up to 24 hours. The attacker recovers their principal (minus the fee charged by `feeBps`) on each cycle, making the attack cheap to sustain.

Impact: **Medium — Temporary freezing of funds** (deposit access blocked for up to 24 hours).

### Likelihood Explanation

The attack requires holding `WHITELISTED_USER_ROLE`, which must be granted by the admin. However, this role is architecturally separate from `OPERATOR_ROLE` and is designed to be extended to external parties. A whitelisted user who turns adversarial, or a role granted to a compromised external address, is a realistic scenario. The attacker only needs enough ETH to cover the fee on each cycle; the principal is recovered each time.

Likelihood: **Low-Medium**.

### Recommendation

Decrement `dailyMintAmount` inside `swapAssetToPremintedRsETH()` by the rsETH amount being returned, mirroring the increment performed in `limitDailyMint`. Specifically, after computing `ethAmount`, calculate the equivalent rsETH amount that was originally minted for that ETH and subtract it from `dailyMintAmount` (clamping to zero to avoid underflow). This restores the daily mint capacity when a reverse swap undoes a prior deposit, exactly as the Pods Finance fix restored the spending cap on refund.

### Proof of Concept

1. Attacker holds `WHITELISTED_USER_ROLE` and a sufficient ETH balance.
2. Attacker calls `deposit{value: X}(referralId)` → `dailyMintAmount` increases by `rsETHAmount`; attacker receives `wrsETH`.
3. Attacker calls `swapAssetToPremintedRsETH(rsETH, rsETHAmount)` → attacker returns `wrsETH` to the wrapper and receives `~X` ETH back; `dailyMintAmount` is **unchanged**.
4. Attacker repeats steps 2–3 until `dailyMintAmount >= dailyMintLimit`.
5. All subsequent calls to `deposit()` by any user revert with `DailyMintLimitExceeded` until the next day's reset (`getCurrentDay() > lastMintDay`). [6](#0-5) [3](#0-2)

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L89-91)
```text
    /// @notice The whitelisted user role identifier
    bytes32 public constant WHITELISTED_USER_ROLE = keccak256("WHITELISTED_USER_ROLE");

```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L104-126)
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L130-135)
```text
    modifier onlyOperatorOrWhitelisted(address account) {
        if (!hasRole(OPERATOR_ROLE, account) && !hasRole(WHITELISTED_USER_ROLE, account)) {
            revert NotOperatorOrWhitelisted();
        }
        _;
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
