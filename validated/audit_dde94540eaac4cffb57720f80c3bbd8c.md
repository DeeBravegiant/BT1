### Title
Stale `rsETHPrice` After Circuit Breaker Trip Enables Over-Redemption via `instantWithdrawal` - (File: `contracts/LRTOracle.sol`, `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` pauses all protocol contracts when the downside protection circuit breaker trips, but **returns early without writing the new lower price to `rsETHPrice`**. The stored value remains at the pre-drop (higher) level. After the admin unpauses the withdrawal manager, `instantWithdrawal` computes payouts using the stale higher `rsETHPrice` with no freshness check, allowing any rsETH holder to redeem at an inflated rate and extract more assets than the current rsETH value warrants.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` contains a downside protection circuit breaker. When `newRsETHPrice` falls more than `pricePercentageLimit` below `highestRsethPrice`, the function pauses the deposit pool, withdrawal manager, and oracle, then returns at line 281 **before reaching the `rsETHPrice = newRsETHPrice` assignment at line 313**:

```solidity
// LRTOracle.sol lines 277–282
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;   // ← rsETHPrice is NOT updated
}
// ...
rsETHPrice = newRsETHPrice;   // line 313 — never reached on circuit-breaker path
```

The stored `rsETHPrice` therefore remains at the pre-drop value.

After the admin unpauses the withdrawal manager (a normal operational step to restore user access), `instantWithdrawal` is immediately callable. It computes the payout via `getExpectedAssetAmount`, which reads the cached `rsETHPrice` with no staleness or freshness guard:

```solidity
// LRTWithdrawalManager.sol line 228
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);

// LRTWithdrawalManager.sol line 593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

Unlike the queued withdrawal path (`initiateWithdrawal` → `unlockQueue` → `_calculatePayoutAmount`), which caps the payout at `min(expectedAssetAmount, currentReturn)` at unlock time, `instantWithdrawal` pays out immediately and unconditionally at whatever `rsETHPrice` is stored. There is no mechanism to prevent it from using the stale pre-drop value.

---

### Impact Explanation

An rsETH holder can burn rsETH and receive assets computed at the stale (higher) `rsETHPrice` rather than the actual post-drop value. If `rsETHPrice` is stale by X% above the true value, the attacker extracts X% more assets per rsETH burned than they are entitled to. This is a direct loss of reserve assets from the protocol — equivalent to the external report's scenario of "minting at a depressed price and redeeming at a higher price, extracting reserve assets," but in reverse direction (redeeming at an inflated price after a slashing event).

**Impact class**: High — direct theft of protocol reserve assets (analogous to theft of unclaimed yield / protocol insolvency at scale).

---

### Likelihood Explanation

Two conditions are required:

1. **A price drop large enough to trip the circuit breaker** — a realistic slashing event on EigenLayer or a temporary oracle deviation can cause `newRsETHPrice` to fall below `highestRsethPrice * (1 - pricePercentageLimit)`. This is the intended trigger for the circuit breaker.

2. **Admin unpauses the withdrawal manager before calling `updateRSETHPrice()`** — after a circuit breaker trip, the admin must unpause the oracle first, then call `updateRSETHPrice()`, then unpause the other contracts. If the admin unpauses the withdrawal manager (to restore user access) before completing the price update, the window opens. This is a realistic operational sequencing mistake, especially under time pressure during an incident.

The oracle's own `whenNotPaused` guard on `updateRSETHPrice()` means the admin must explicitly unpause the oracle before the price can be refreshed, creating a natural gap between "contracts unpaused" and "price updated."

---

### Recommendation

1. **Update `rsETHPrice` before pausing**: In the circuit breaker branch, write `rsETHPrice = newRsETHPrice` before calling `_pause()` and returning. This ensures the stored price always reflects the latest computed value, even when the circuit breaker fires.

2. **Add a price-freshness guard to `instantWithdrawal`**: Require that `rsETHPrice` was updated within a maximum staleness window (e.g., 1 hour) before allowing instant redemption. Revert if the price is stale.

3. **Enforce unpausing order in documentation/tooling**: Require that `updateRSETHPrice()` is called and succeeds before the withdrawal manager is unpaused, ideally in a single atomic admin transaction.

---

### Proof of Concept

1. `rsETHPrice` is `1.05e18` (1.05 ETH per rsETH). `highestRsethPrice` is `1.05e18`. `pricePercentageLimit` is `0.05e18` (5%).

2. A slashing event reduces the protocol's total ETH. `updateRSETHPrice()` is called; `_updateRsETHPrice()` computes `newRsETHPrice = 0.99e18`.

3. `diff = 1.05e18 - 0.99e18 = 0.06e18 > 0.05 * 1.05e18 = 0.0525e18` → circuit breaker trips.

4. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()` are called. Function returns at line 281. **`rsETHPrice` remains `1.05e18`.**

5. Admin unpauses the withdrawal manager to allow pending withdrawals to complete (before remembering to refresh the oracle price).

6. Attacker holds 1000 rsETH. Calls `instantWithdrawal(ETH_TOKEN, 1000e18, "")`.

7. `getExpectedAssetAmount` returns `1000e18 * 1.05e18 / 1e18 = 1050 ETH` (using stale `rsETHPrice`).

8. Attacker burns 1000 rsETH and receives 1050 ETH (minus fee). True value at current price: 990 ETH. **Protocol loses ~60 ETH** (6%) on this single redemption.

9. Admin later calls `updateRSETHPrice()` (after unpausing the oracle), which writes `rsETHPrice = 0.99e18`. The window is closed, but the loss has already occurred. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L277-282)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTWithdrawalManager.sol (L212-228)
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
