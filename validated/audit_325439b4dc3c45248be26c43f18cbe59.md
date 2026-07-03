### Title
Unprivileged Caller Can Trigger Protocol-Wide Pause via Public `updateRSETHPrice()` - (File: contracts/LRTOracle.sol)

---

### Summary

`updateRSETHPrice()` in `LRTOracle.sol` is `public` with no access control beyond `whenNotPaused`. Its internal logic contains a downside-protection branch that, when triggered, pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself. Any unprivileged caller can invoke this function at a moment when the oracle-reported rsETH price has temporarily dipped below the stored `highestRsethPrice` by more than `pricePercentageLimit`, causing a protocol-wide pause and temporarily freezing all user deposits and withdrawals.

---

### Finding Description

`updateRSETHPrice()` is declared `public` with only the `whenNotPaused` modifier:

```solidity
// LRTOracle.sol line 87
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Inside `_updateRsETHPrice()`, the downside-protection branch at lines 270–282 reads:

```solidity
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
```

`highestRsethPrice` is a monotonically increasing watermark — it is only ever updated upward (line 294–296). As the protocol accumulates staking rewards over time, `highestRsethPrice` grows. Any temporary downward movement in the underlying LST oracle prices (e.g., a brief Chainlink price update lag, a market dip, or a partial depeg event) causes `newRsETHPrice` to fall below `highestRsethPrice`. If the drop exceeds `pricePercentageLimit` (a configurable admin parameter), the branch fires.

Because `updateRSETHPrice()` is callable by anyone, an attacker can monitor the oracle prices off-chain and submit a call at the exact moment the condition is satisfied — without needing any special role, capital, or oracle manipulation.

The contrast with the privileged path is explicit in the codebase: `updateRSETHPriceAsManager()` (line 94) is `external onlyLRTManager`, showing the developers intended a restricted caller for sensitive price updates, yet the public variant carries the same powerful side-effect.

---

### Impact Explanation

When the downside-protection branch fires, three contracts are paused atomically:

- `LRTDepositPool` — `depositETH()` and `depositAsset()` both carry `whenNotPaused`, blocking all new L1 deposits.
- `LRTWithdrawalManager` — `initiateWithdrawal()`, `completeWithdrawal()`, and `instantWithdrawal()` all carry `whenNotPaused`, blocking all withdrawal initiation and completion.
- `LRTOracle` itself — `updateRSETHPrice()` becomes uncallable until admin unpauses.

Unpausing each contract requires a separate admin (`onlyLRTAdmin`) call to `unpause()` on `LRTDepositPool` (line 354), `LRTWithdrawalManager` (line 352), and `LRTOracle` (line 143). Until those three transactions are confirmed, all user funds in transit (pending withdrawals, queued deposits) are frozen.

**Impact: Temporary freezing of funds.**

---

### Likelihood Explanation

The trigger condition — a price drop exceeding `pricePercentageLimit` from the all-time-high rsETH price — is a realistic market event. LST prices fluctuate due to oracle update cadence, brief depegs, or normal volatility. The attacker requires no capital, no privileged role, and no oracle manipulation: they only need to observe the public oracle price feeds and submit a single zero-value transaction at the right moment. The cost is one gas payment; the effect is a full protocol halt until three admin transactions respond.

---

### Recommendation

Restrict `updateRSETHPrice()` to a trusted caller (e.g., a keeper with `MANAGER` or a dedicated `KEEPER_ROLE`), mirroring the access control already applied to `updateRSETHPriceAsManager()`. Alternatively, separate the price-read path (view-only) from the pause-triggering path, and gate the pause-triggering path behind a role check.

---

### Proof of Concept

1. Protocol has been running for several months; `highestRsethPrice = 1.05e18` (rsETH has appreciated 5%).
2. Admin sets `pricePercentageLimit = 1e16` (1%).
3. A brief Chainlink stETH/ETH price update causes the computed `newRsETHPrice = 1.034e18` (a ~1.5% drop from the watermark, exceeding the 1% limit).
4. Attacker calls `LRTOracle.updateRSETHPrice()` — no role required, no ETH value needed.
5. `_updateRsETHPrice()` computes `diff = 1.05e18 - 1.034e18 = 0.016e18 > 0.01 * 1.05e18 = 0.0105e18` → `isPriceDecreaseOffLimit = true`.
6. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()` are called.
7. All `depositETH`, `depositAsset`, `initiateWithdrawal`, `completeWithdrawal`, and `instantWithdrawal` calls revert with `Paused`.
8. Users cannot access their funds until the admin manually unpauses all three contracts.

**Relevant code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L85-89)
```text
    /// @notice updates RSETH/ETH exchange rate
    /// @dev calculates rsETH price based on stakedAsset value received from EigenLayer
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L269-282)
```text
        // downside protection — pause if price drops too far
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L293-296)
```text
        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
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

**File:** contracts/LRTWithdrawalManager.sol (L150-185)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }

    /// @notice Completes a user's withdrawal process by transferring the ETH/LST amount corresponding to the rsETH
    /// unstaked.
    /// @param asset The asset address the user wishes to withdraw.
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```
