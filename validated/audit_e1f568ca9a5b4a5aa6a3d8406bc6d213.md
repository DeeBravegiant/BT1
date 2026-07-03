### Title
Stale `currentPeriodMintedAmount` Never Decremented on rsETH Burns Allows Premature Daily Mint Limit Exhaustion - (File: `contracts/RSETH.sol`)

---

### Summary

`RSETH.sol` tracks cumulative minted rsETH in `currentPeriodMintedAmount` and enforces a daily cap via the `checkDailyMintLimit` modifier. However, `burnFrom()` — the counterpart operation — never decrements this counter. When rsETH is burned within the same 24-hour period it was minted (e.g., via `LRTWithdrawalManager.instantWithdrawal()`), the counter remains inflated. Subsequent mints in the same period see a stale, over-counted total, causing the daily limit to be hit prematurely and blocking all new deposits for up to 24 hours.

---

### Finding Description

In `contracts/RSETH.sol`, the `checkDailyMintLimit` modifier unconditionally increments `currentPeriodMintedAmount` on every `mint()` call:

```solidity
// contracts/RSETH.sol lines 42-56
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

The `burnFrom()` function, however, performs no corresponding decrement:

```solidity
// contracts/RSETH.sol lines 245-248
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _burn(account, amount);
}
```

`burnFrom` is called by `LRTWithdrawalManager.instantWithdrawal()`, which is a **user-callable** function (when enabled by the manager):

```solidity
// contracts/LRTWithdrawalManager.sol line 229
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

This means a user can:
1. Call `LRTDepositPool.depositETH()` → rsETH minted → `currentPeriodMintedAmount += X`
2. Call `LRTWithdrawalManager.instantWithdrawal()` → rsETH burned → `currentPeriodMintedAmount` unchanged (still +X)
3. Repeat until `currentPeriodMintedAmount` reaches `maxMintAmountPerDay`
4. All subsequent `mint()` calls revert with `DailyMintLimitExceeded` for the rest of the period

The counter is only reset at the start of the next 24-hour period. The protocol's underlying balance is unaffected, but the mint accounting is permanently overstated within the period, mirroring the original bug's pattern of a stale cumulative total distorting subsequent operations.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

New depositors are unable to mint rsETH for up to 24 hours. The `LRTDepositPool.depositETH()` and `depositAsset()` flows both call `IRSETH.mint()`, which will revert for the remainder of the period once the inflated `currentPeriodMintedAmount` reaches `maxMintAmountPerDay`. User funds are not lost, but the protocol fails to deliver its core promised service (minting rsETH for deposits) for the duration of the period.

---

### Likelihood Explanation

**Low-to-Medium.**

Prerequisites:
- `isInstantWithdrawalEnabled[asset]` must be set to `true` by the manager (a realistic operational state).
- The attacker must spend ETH plus the `instantWithdrawalFee` on each cycle.
- The attacker must cycle enough volume to exhaust `maxMintAmountPerDay`.

Once instant withdrawal is enabled, any user can execute this attack permissionlessly. The economic cost scales with `maxMintAmountPerDay`, but the attacker recovers most of their ETH principal on each cycle (minus fees), making the net cost only the fee paid per cycle. For a sufficiently low fee setting, the attack becomes economically viable as a griefing vector.

---

### Recommendation

Decrement `currentPeriodMintedAmount` in `burnFrom()` by the burned amount, capped at the current period's minted amount to avoid underflow:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    // Reduce the period counter, but only for burns within the current period
    if (block.timestamp < periodStartTime + 1 days) {
        currentPeriodMintedAmount = amount > currentPeriodMintedAmount
            ? 0
            : currentPeriodMintedAmount - amount;
    }
    _burn(account, amount);
}
```

---

### Proof of Concept

Assume `maxMintAmountPerDay = 1000 ether`, `instantWithdrawalFee = 10 bps`, and instant withdrawal is enabled for ETH.

1. Attacker calls `LRTDepositPool.depositETH{value: 1000 ether}()` → 1000 rsETH minted → `currentPeriodMintedAmount = 1000 ether`.
2. Attacker calls `LRTWithdrawalManager.instantWithdrawal(ETH, 1000 rsETH)` → 1000 rsETH burned → `currentPeriodMintedAmount` remains `1000 ether`. Attacker receives ~999 ETH back (minus fee).
3. Any subsequent call to `RSETH.mint()` within the same period reverts:
   ```
   DailyMintLimitExceeded(1000 ether + newAmount, 1000 ether)
   ```
4. All new depositors are blocked from minting rsETH for up to 24 hours.

The attacker spent ~1 ETH in fees to freeze the daily minting window for all other users. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/RSETH.sol (L245-248)
```text
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
