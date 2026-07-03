### Title
Unrestricted `updateRSETHPrice()` Allows Any Caller to Trigger Protocol-Wide Pause — (`File: contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard and no caller restriction. Any externally owned account can invoke it at will. When the computed rsETH price has fallen more than `pricePercentageLimit` below `highestRsethPrice`, the function unconditionally pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself, temporarily freezing all user deposits and withdrawals.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is the public entry point that recalculates and stores the rsETH/ETH exchange rate, mints protocol fees, and enforces downside-protection pausing:

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

Inside `_updateRsETHPrice()`, if the freshly computed price is more than `pricePercentageLimit` below `highestRsethPrice`, the function pauses all three contracts:

```solidity
// contracts/LRTOracle.sol:270-282
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
``` [2](#0-1) 

The stored `rsETHPrice` is the denominator used by `LRTDepositPool.getRsETHAmountToMint()` for every deposit:

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

By contrast, the privileged variant `updateRSETHPriceAsManager()` is correctly gated:

```solidity
// contracts/LRTOracle.sol:94-96
function updateRSETHPriceAsManager() external onlyLRTManager {
    _updateRsETHPrice();
}
``` [4](#0-3) 

The existence of this privileged variant confirms the protocol authors intended restricted access for price-update calls, yet the public variant was left unrestricted.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

When the on-chain TVL has genuinely declined beyond `pricePercentageLimit` (e.g., after an EigenLayer slashing event or a large LST depeg), the pause condition inside `_updateRsETHPrice()` becomes satisfiable. Because `updateRSETHPrice()` is unrestricted, any attacker can call it at a strategically chosen moment to atomically pause `LRTDepositPool` and `LRTWithdrawalManager`. All pending deposits and withdrawal claims are frozen until an admin with `DEFAULT_ADMIN_ROLE` manually unpauses each contract. Users cannot deposit or redeem rsETH during this window. [5](#0-4) 

---

### Likelihood Explanation

**Medium.** EigenLayer slashing, LST depegs, or protocol-level losses are realistic events that can push the computed price below `highestRsethPrice` by more than `pricePercentageLimit`. Once that condition exists on-chain, any observer can trigger the pause in a single transaction with no capital requirement. The attacker's only cost is gas. The window between the condition becoming true and a legitimate keeper calling the function is the exploitable gap.

---

### Recommendation

Add the same `onlyLRTManager` (or a dedicated keeper role) restriction to `updateRSETHPrice()` that already protects `updateRSETHPriceAsManager()`, or introduce a separate permissioned keeper role. If a fully permissionless price update is desired for liveness, the pause-triggering side-effect should be separated into a distinct, access-controlled function so that the pause cannot be weaponized by an arbitrary caller.

---

### Proof of Concept

1. Assume `pricePercentageLimit = 1e16` (1%) and `highestRsethPrice = 1.05e18`.
2. A slashing event reduces total ETH in protocol such that `_getTotalEthInProtocol()` yields a new price of `1.03e18` — a ~1.9% drop, exceeding the 1% limit.
3. Attacker calls `LRTOracle.updateRSETHPrice()` with no special role.
4. `_updateRsETHPrice()` computes `newRsETHPrice = 1.03e18 < highestRsethPrice = 1.05e18`, `diff = 0.02e18 > 0.01 * 1.05e18 = 0.0105e18` → `isPriceDecreaseOffLimit = true`.
5. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` are called atomically.
6. All user deposits (`depositETH`, `depositAsset`) and withdrawal claims revert with `Pausable: paused` until an admin manually unpauses — freezing user funds for an indeterminate period. [1](#0-0) [2](#0-1) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
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

**File:** contracts/LRTOracle.sol (L270-282)
```text
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
