### Title
No `minAmountExpected` Guard in `instantWithdrawal` Allows Mutable Fee to Silently Reduce User Payout - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager.instantWithdrawal` applies `instantWithdrawalFee` at execution time with no caller-supplied minimum-output guard. Because the fee is mutable by the LRT Manager at any time, a user who previews their expected payout via `getExpectedAssetAmount` and then submits `instantWithdrawal` can receive materially fewer assets than anticipated if the fee is raised between those two steps. The code itself acknowledges this in a developer comment.

### Finding Description

`instantWithdrawalFee` is a global basis-point fee stored in `LRTWithdrawalManager` and freely updatable by any address holding the `LRTManager` role via `setInstantWithdrawalFee`. [1](#0-0) 

The public view function `getExpectedAssetAmount` lets users calculate how many underlying assets their rsETH is worth before they commit to a withdrawal. [2](#0-1) 

`instantWithdrawal` then burns the user's rsETH **before** applying the fee, and the fee deduction uses whatever `instantWithdrawalFee` is at execution time — not the value the user observed during preview: [3](#0-2) 

The function signature accepts no `minAssetAmountExpected` parameter:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    string calldata referralId
)
```

The developers themselves document the exposure in the NatSpec: [4](#0-3) 

> `@dev Uses the fee set at execution time. Managers can raise it right before this call, making withdrawals cost more than expected.`

The same structural gap exists across every L2 pool contract's `deposit` function — `feeBps` is mutable via `setFeeBps` while `deposit` accepts no `minRsETHAmountExpected` parameter — but the `instantWithdrawal` case is more severe because rsETH is burned irreversibly before the fee is deducted. [5](#0-4) [6](#0-5) 

### Impact Explanation

A user who calls `getExpectedAssetAmount` off-chain, computes their expected net payout as `assetAmount - (assetAmount * currentFee / 10_000)`, and then submits `instantWithdrawal` will receive fewer assets than computed if the fee is raised in the interim. The rsETH burn is irreversible; the user cannot undo the withdrawal. The shortfall flows to the protocol treasury / fee recipient rather than back to the user.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

The maximum fee is capped at 10 % (`feeBasisPoints > 1000` reverts), so the worst-case shortfall relative to a zero-fee preview is 10 % of the withdrawn asset amount.

### Likelihood Explanation

The LRT Manager role is a privileged but operationally active role that legitimately adjusts fees. A fee increase that happens to land between a user's preview call and their `instantWithdrawal` execution — whether due to routine protocol maintenance or a mempool-visible pending transaction — will silently reduce the user's payout. No malicious intent is required; the acknowledged developer comment confirms this is a known operational reality.

### Recommendation

Add a `minAssetAmountExpected` parameter to `instantWithdrawal` and revert if `userAmount < minAssetAmountExpected`:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    uint256 minAssetAmountExpected,   // <-- add
    string calldata referralId
) external ... {
    ...
    uint256 userAmount = assetAmountUnlocked - fee;
    if (userAmount < minAssetAmountExpected) revert SlippageExceeded();
    ...
}
```

Apply the same fix to every L2 pool `deposit` function by adding a `minRsETHAmountExpected` parameter checked against the computed `rsETHAmount`.

### Proof of Concept

1. `instantWithdrawalFee` is currently 50 bps (0.5 %). User calls `getExpectedAssetAmount(ETH, 1e18 rsETH)` → 1.05 ETH. User computes net payout: `1.05 ETH - 0.5% = 1.0447 ETH`. User submits `instantWithdrawal(ETH, 1e18, "ref")`.
2. Before the transaction is mined, the LRT Manager calls `setInstantWithdrawalFee(1000)` (10 %).
3. `instantWithdrawal` executes: rsETH is burned, `fee = 1.05 ETH * 10% = 0.105 ETH`, `userAmount = 0.945 ETH`.
4. User receives `0.945 ETH` instead of the expected `1.0447 ETH` — a ~9.5 % shortfall — with no revert and no recourse. [7](#0-6) [1](#0-0)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L210-211)
```text
    /// @dev Uses the fee set at execution time. Managers can raise it right before this call, making withdrawals cost
    /// more than expected.
```

**File:** contracts/LRTWithdrawalManager.sol (L228-250)
```text
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
```

**File:** contracts/LRTWithdrawalManager.sol (L372-375)
```text
    function setInstantWithdrawalFee(uint256 feeBasisPoints) external onlyLRTManager {
        if (feeBasisPoints > 1000) revert FeeTooHigh(); // Max 10%
        instantWithdrawalFee = feeBasisPoints;
        emit InstantWithdrawalFeeUpdated(feeBasisPoints);
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
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

**File:** contracts/pools/RSETHPool.sol (L574-577)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
```
