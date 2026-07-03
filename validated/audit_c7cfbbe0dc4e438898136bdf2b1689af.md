### Title
`burnFrom()` Does Not Decrement `currentPeriodMintedAmount`, Causing Premature Daily Mint Limit Exhaustion - (File: contracts/RSETH.sol)

### Summary

The `RSETH.sol` contract tracks minted rsETH in a `currentPeriodMintedAmount` counter that is incremented on every `mint()` call but is never decremented when rsETH is burned via `burnFrom()`. This mirrors the exact vulnerability class from the external report: a supply-tracking counter is updated on creation but not on destruction, causing the accounting to diverge from reality. The result is that the daily mint limit is artificially consumed by burned tokens, temporarily blocking all new rsETH deposits until the next 24-hour period resets.

### Finding Description

In `contracts/RSETH.sol`, the `checkDailyMintLimit` modifier increments `currentPeriodMintedAmount` on every `mint()` call:

```solidity
modifier checkDailyMintLimit(uint256 amount) {
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        ...
    }
    if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
        revert DailyMintLimitExceeded(...);
    }
    currentPeriodMintedAmount += amount;  // incremented on mint
    _;
}
``` [1](#0-0) 

However, the `burnFrom()` function only calls `_burn()` (which correctly decrements the ERC20 `totalSupply`) but makes no adjustment to `currentPeriodMintedAmount`:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _burn(account, amount);  // currentPeriodMintedAmount is NOT decremented
}
``` [2](#0-1) 

The unprivileged entry path is `LRTWithdrawalManager.instantWithdrawal()`, which is callable by any user when instant withdrawal is enabled for an asset. It burns rsETH directly from the caller:

```solidity
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
``` [3](#0-2) 

Additionally, the operator-triggered `unlockQueue()` also burns rsETH via `burnFrom()` without any decrement to `currentPeriodMintedAmount`:

```solidity
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
``` [4](#0-3) 

**Attack scenario (when instant withdrawal is enabled):**

1. Attacker deposits ETH via `LRTDepositPool.depositETH()` → `currentPeriodMintedAmount += X`
2. Attacker calls `LRTWithdrawalManager.instantWithdrawal()` → rsETH is burned, but `currentPeriodMintedAmount` remains at `X`
3. Attacker repeats steps 1–2 until `currentPeriodMintedAmount` reaches `maxMintAmountPerDay`
4. All subsequent `mint()` calls revert with `DailyMintLimitExceeded` until the 24-hour period resets

**Natural protocol operation scenario:**

Even without a deliberate attacker, normal operation causes this: users deposit rsETH, operators call `unlockQueue()` which burns rsETH, and `currentPeriodMintedAmount` is never reduced. Over time within a single day, the effective remaining mint capacity is lower than it should be.

### Impact Explanation

The daily mint limit in `RSETH.sol` is a security mechanism intended to cap the amount of rsETH that can be minted in a 24-hour window. Because `currentPeriodMintedAmount` is never decremented on burn, the counter overstates the true net minted supply. This causes `DailyMintLimitExceeded` reverts for legitimate depositors even when the actual outstanding rsETH supply is well below the intended cap. All L1 deposits via `LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` are blocked until the next period reset — a temporary freezing of the deposit functionality. [5](#0-4) 

### Likelihood Explanation

The griefing path via `instantWithdrawal()` requires the manager to have enabled instant withdrawal for at least one asset (`isInstantWithdrawalEnabled[asset] = true`). When enabled, any user can execute the attack at the cost of the `instantWithdrawalFee` (up to 10%). The natural drift (from `unlockQueue()` burning rsETH) occurs unconditionally during normal protocol operation whenever withdrawals are processed within the same 24-hour window as deposits, which is a routine occurrence. [6](#0-5) 

### Recommendation

Decrement `currentPeriodMintedAmount` in `burnFrom()` to mirror the burn against the daily counter, clamping at zero to avoid underflow:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _burn(account, amount);
    if (currentPeriodMintedAmount >= amount) {
        currentPeriodMintedAmount -= amount;
    } else {
        currentPeriodMintedAmount = 0;
    }
}
```

This ensures the daily mint counter accurately reflects the net outstanding rsETH minted within the current period, consistent with the intent of the security mechanism.

### Proof of Concept

1. Assume `maxMintAmountPerDay = 1000 ether` and instant withdrawal is enabled for ETH.
2. Attacker calls `LRTDepositPool.depositETH{value: 1000 ether}()` → `RSETH.mint()` is called → `currentPeriodMintedAmount = 1000 ether`.
3. Attacker calls `LRTWithdrawalManager.instantWithdrawal(ETH, 1000 ether, "")` → `RSETH.burnFrom()` is called → ERC20 `totalSupply` decreases by 1000 ether, but `currentPeriodMintedAmount` remains at `1000 ether`.
4. Legitimate user calls `LRTDepositPool.depositETH{value: 1 ether}()` → `RSETH.mint(user, 1 ether)` → `checkDailyMintLimit` checks `1000 ether + 1 ether > 1000 ether` → reverts with `DailyMintLimitExceeded`.
5. All deposits are blocked for the remainder of the 24-hour period despite the actual rsETH `totalSupply` being 0. [7](#0-6) [8](#0-7)

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

**File:** contracts/RSETH.sol (L245-248)
```text
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L212-222)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L224-235)
```text
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
```

**File:** contracts/LRTWithdrawalManager.sol (L305-305)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```
