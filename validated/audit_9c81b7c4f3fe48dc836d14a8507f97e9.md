### Title
`RSETH::currentPeriodMintedAmount` Not Decremented on Burns Allows Daily Mint Limit Exhaustion via Deposit-Withdraw Cycling - (File: contracts/RSETH.sol)

### Summary
`RSETH::checkDailyMintLimit` increments `currentPeriodMintedAmount` on every `mint()` call, but `RSETH::burnFrom()` never decrements it. Because `LRTWithdrawalManager::instantWithdrawal()` is a user-accessible function that burns rsETH, any user can cycle deposit → instant-withdraw within the same 24-hour window to exhaust the daily mint cap, temporarily blocking all other depositors from minting rsETH for the rest of that day.

### Finding Description
`RSETH.sol` enforces a per-day minting cap via the `checkDailyMintLimit` modifier:

```solidity
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

`currentPeriodMintedAmount` is incremented on every `mint()` call. However, `burnFrom()` performs no corresponding decrement:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _burn(account, amount);   // currentPeriodMintedAmount is never touched
}
```

`LRTWithdrawalManager::instantWithdrawal()` is callable by any user (when instant withdrawal is enabled for an asset) and internally calls `IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked)`. This creates a fully user-accessible path that burns rsETH without restoring any capacity to the daily mint counter.

### Impact Explanation
**Medium — Temporary freezing of funds.**

Within a single 24-hour period a user can:
1. Deposit ETH → rsETH minted → `currentPeriodMintedAmount` increases.
2. Call `instantWithdrawal` → rsETH burned → `currentPeriodMintedAmount` unchanged.
3. Repeat until `currentPeriodMintedAmount >= maxMintAmountPerDay`.

After this, every subsequent `mint()` call by any user reverts with `DailyMintLimitExceeded` until the period resets. All legitimate depositors are locked out of minting rsETH for up to 24 hours. The attacker recovers their principal (minus instant-withdrawal fees) on each cycle, so the cost is bounded by fees rather than capital.

### Likelihood Explanation
**Medium.** Instant withdrawal must be enabled for at least one asset (a manager-controlled flag). Once enabled, the attack requires no special role — any depositor can execute it. The fee cost per cycle is a deterrent but not a blocker; a motivated actor (e.g., a competitor or griever) can absorb fees to deny service for a full day. The daily limit is a protocol-wide cap, so a single attacker cycling a moderate amount can exhaust it before other users act.

### Recommendation
Decrement `currentPeriodMintedAmount` inside `burnFrom()` when the burn occurs within the active period:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    // Restore capacity if still within the current period
    if (block.timestamp < periodStartTime + 1 days) {
        if (currentPeriodMintedAmount >= amount) {
            currentPeriodMintedAmount -= amount;
        } else {
            currentPeriodMintedAmount = 0;
        }
    }
    _burn(account, amount);
}
```

Alternatively, track the daily mint limit against `totalSupply()` (net minted minus burned) rather than a cumulative counter, mirroring the fix applied in the referenced Securitize report.

### Proof of Concept
Assume `maxMintAmountPerDay = 1000 rsETH`, instant withdrawal is enabled for ETH, and the current period has 900 rsETH already minted.

1. Alice deposits 100 ETH → `LRTDepositPool.depositETH()` → `RSETH.mint(alice, 100)` → `currentPeriodMintedAmount = 1000`.
2. Alice calls `LRTWithdrawalManager.instantWithdrawal(ETH, 100, "")` → `RSETH.burnFrom(alice, 100)` → `currentPeriodMintedAmount` remains `1000`.
3. Bob tries to deposit 1 ETH → `RSETH.mint(bob, 1)` → `currentPeriodMintedAmount + 1 > maxMintAmountPerDay` → **`DailyMintLimitExceeded` revert**.
4. All minting is frozen until the 24-hour period resets.

Alice recovers her ETH (minus the instant-withdrawal fee). Bob and all other depositors are blocked for up to 24 hours. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** contracts/RSETH.sol (L229-248)
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

    /// @notice Burns rsETH when called by an authorized caller
    /// @param account the account to burn from
    /// @param amount the amount of rsETH to burn
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```
