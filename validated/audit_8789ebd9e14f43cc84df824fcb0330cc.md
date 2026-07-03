### Title
Unprivileged Depositor Can Exhaust Global Daily rsETH Mint Limit, Temporarily Blocking All L1 Deposits - (File: contracts/RSETH.sol)

### Summary
The `RSETH.checkDailyMintLimit` modifier maintains a single global `currentPeriodMintedAmount` counter shared across every holder of `MINTER_ROLE`. Because `LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` are publicly callable with no per-user cap, any sufficiently funded depositor can exhaust `maxMintAmountPerDay` in one or a few transactions, causing every subsequent deposit call to revert with `DailyMintLimitExceeded` for up to 24 hours.

### Finding Description
`RSETH.mint()` applies `checkDailyMintLimit(amount)` before minting:

```solidity
// contracts/RSETH.sol
if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
    revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
}
currentPeriodMintedAmount += amount;
```

This counter is global — it accumulates contributions from every authorized minter without any per-caller partitioning. `LRTDepositPool` holds `MINTER_ROLE` and calls `IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)` inside `_mintRsETH()`, which is invoked by the publicly accessible `depositETH()` and `depositAsset()` functions. Neither function carries a per-user or per-transaction rsETH cap; the only ceiling is the per-asset `depositLimitByAsset` in `LRTConfig`, which is a cumulative protocol-wide ceiling, not a per-depositor limit.

An attacker who deposits an amount whose rsETH equivalent equals or exceeds the remaining `maxMintAmountPerDay` fills the counter. All subsequent calls to `RSETH.mint()` — from other retail depositors or from the `L1Vault`/`L1VaultV2` manager-triggered deposit flows that also route through `LRTDepositPool` — revert until the 24-hour window resets.

This is the direct analog of the reference report: just as an external minter can occupy NFT IDs that `NFTMintSaleMultiple` reserved for sale, an unprivileged depositor here can consume the shared minting budget that all other depositors depend on, causing the same class of shared-resource conflict that makes the core function revert.

### Impact Explanation
All L1 rsETH minting is blocked for up to 24 hours. Users who attempt to deposit ETH or LSTs via `LRTDepositPool` receive a revert. This constitutes a temporary freezing of the deposit path — a Medium impact per the allowed scope.

### Likelihood Explanation
The attacker receives rsETH in return for their deposit (no net loss of principal) and can recover funds via `LRTWithdrawalManager.initiateWithdrawal()` or `instantWithdrawal()` (paying only the instant-withdrawal fee). The attack is repeatable every 24 hours at the cost of that fee. A well-capitalised actor can sustain this indefinitely, making likelihood realistic.

### Recommendation
- Introduce a per-transaction or per-user minting cap at the `LRTDepositPool` level so that no single depositor can consume the entire daily budget.
- Alternatively, track the daily limit per authorized minter rather than globally, so that one minter's activity cannot crowd out another.
- Clarify whether `maxMintAmountPerDay == 0` should mean "unlimited" (add an explicit bypass) rather than "zero allowed", to avoid an accidental total mint lockout on deployment.

### Proof of Concept
1. Manager sets `maxMintAmountPerDay = X` (e.g., 1 000 ETH worth of rsETH).
2. Attacker calls `LRTDepositPool.depositETH{value: Y}(0, "")` where `Y` is large enough that `getRsETHAmountToMint(ETH_TOKEN, Y) >= X`.
3. `_mintRsETH()` → `RSETH.mint()` → `checkDailyMintLimit` sets `currentPeriodMintedAmount = X`.
4. Any subsequent call by another user to `depositETH` or `depositAsset` triggers:
   ```
   revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
   ```
5. The deposit path is frozen for up to 24 hours.
6. Attacker queues withdrawal via `LRTWithdrawalManager.initiateWithdrawal()` or uses `instantWithdrawal()` (paying the fee) to recover principal immediately.

**Relevant code locations:**

`RSETH.checkDailyMintLimit` — shared global counter: [1](#0-0) 

`RSETH.mint` — publicly reachable via `LRTDepositPool`, gated only by `MINTER_ROLE` (held by `LRTDepositPool`): [2](#0-1) 

`LRTDepositPool._mintRsETH` — calls `RSETH.mint` with no per-user cap: [3](#0-2) 

`LRTDepositPool.depositETH` — publicly callable entry point: [4](#0-3) 

`LRTDepositPool.depositAsset` — publicly callable entry point: [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```
