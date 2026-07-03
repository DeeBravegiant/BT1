### Title
Delayed `updateRSETHPrice` Transaction Triggers Unexpected Protocol-Wide Pause - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function. Its internal logic compares the freshly computed price against `highestRsethPrice` (an all-time-high stored value). If a `updateRSETHPrice()` transaction is delayed in the mempool while an intervening call raises `highestRsethPrice`, the delayed transaction executes against a higher baseline and can cross the `pricePercentageLimit` downside threshold, triggering an unexpected pause of `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself.

### Finding Description

`updateRSETHPrice()` has no access control and is callable by any address: [1](#0-0) 

Inside `_updateRsETHPrice()`, the downside-protection logic pauses the entire protocol when the freshly computed price falls below `highestRsethPrice` by more than `pricePercentageLimit`: [2](#0-1) 

`highestRsethPrice` is a persistent all-time-high that is updated upward whenever a successful price update records a new peak: [3](#0-2) 

**Attack / Delay Scenario (concrete):**

Assume `highestRsethPrice = 1.00 ETH`, `pricePercentageLimit = 1%` (1e16).

1. Current price = 1.005 ETH (within threshold). A caller submits `updateRSETHPrice()` with a low gas price; the transaction sits in the mempool.
2. Before the delayed tx lands, the price rises to 1.02 ETH. A second `updateRSETHPrice()` call executes successfully, updating `highestRsethPrice = 1.02 ETH`.
3. The price then drifts back to 1.005 ETH (normal market movement, still within 1% of the original peak).
4. The delayed transaction from step 1 now executes. It computes `newRsETHPrice = 1.005 ETH` against `highestRsethPrice = 1.02 ETH`. The difference is `0.015 / 1.02 ≈ 1.47%`, which exceeds the 1% threshold.
5. The protocol pauses: `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, and `LRTOracle._pause()` are all called. [4](#0-3) 

The pause affects both deposits and withdrawals: [5](#0-4) [6](#0-5) 

### Impact Explanation

When the protocol is paused, all user-facing operations in `LRTDepositPool` (`depositETH`, `depositAsset`) and `LRTWithdrawalManager` (`initiateWithdrawal`, `completeWithdrawal`, `instantWithdrawal`) revert. Funds already in the withdrawal queue are temporarily frozen until an admin manually unpauses. This constitutes **temporary freezing of funds** (Medium severity).

### Likelihood Explanation

- `updateRSETHPrice()` is permissionless; any address can submit it.
- Ethereum mempool congestion routinely delays low-gas transactions by minutes to hours.
- The scenario requires only normal price volatility (a small rise followed by a return to prior levels) combined with a delayed transaction — no adversarial action is required. A legitimate keeper bot submitting with insufficient gas is sufficient.
- The condition is more likely when `pricePercentageLimit` is set tightly (e.g., 0.5–1%), which is the intended operational configuration for downside protection.

### Recommendation

Add a `deadline` (or `maxTimestamp`) parameter to `updateRSETHPrice()`. If `block.timestamp > deadline`, revert without pausing the protocol. This mirrors the fix recommended in the reference report and ensures stale transactions fail cleanly rather than triggering a pause:

```solidity
function updateRSETHPrice(uint256 deadline) public whenNotPaused {
    if (block.timestamp > deadline) revert TransactionExpired();
    _updateRsETHPrice();
}
```

Alternatively, record the `highestRsethPrice` snapshot at transaction submission time and pass it as a parameter, reverting if the on-chain value has moved beyond an acceptable range since submission.

### Proof of Concept

1. Deploy with `pricePercentageLimit = 1e16` (1%).
2. Set initial state: `rsETHPrice = highestRsethPrice = 1.00e18`.
3. Submit `updateRSETHPrice()` tx with low gas when current computed price = `1.005e18`. Tx enters mempool but does not land.
4. Price rises; a second `updateRSETHPrice()` lands, setting `highestRsethPrice = 1.02e18`.
5. Price returns to `1.005e18`.
6. The delayed tx from step 3 lands. `_updateRsETHPrice()` computes `newRsETHPrice = 1.005e18`. Check: `diff = 1.02e18 - 1.005e18 = 0.015e18`. `pricePercentageLimit.mulWad(highestRsethPrice) = 0.01e18 * 1.02 = 0.0102e18`. Since `0.015e18 > 0.0102e18`, `isPriceDecreaseOffLimit = true`.
7. `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, and `LRTOracle._pause()` are called — protocol is frozen. [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
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

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
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
```
