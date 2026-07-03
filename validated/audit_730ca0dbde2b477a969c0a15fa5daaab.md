### Title
Downside Protection in `_updateRsETHPrice()` Does Not Account for Legitimate EigenLayer Slashing Losses, Causing Temporary Freeze of User Withdrawals - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._updateRsETHPrice()` contains an automatic downside-protection mechanism that pauses the entire protocol (deposit pool, withdrawal manager, and oracle) whenever the computed rsETH price drops more than `pricePercentageLimit` below `highestRsethPrice` (the all-time high). This mechanism does not distinguish between legitimate, known loss scenarios — specifically EigenLayer operator slashing — and malicious price manipulation. When slashing occurs and the price drop exceeds the configured threshold, any public caller of `updateRSETHPrice()` triggers a protocol-wide pause, temporarily freezing all user withdrawals.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` computes a new rsETH price and compares it against `highestRsethPrice`, the all-time high stored since deployment:

```solidity
// downside protection — pause if price drops too far
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
``` [1](#0-0) 

The threshold is `pricePercentageLimit × highestRsethPrice`. Because `highestRsethPrice` is the all-time high and is never reset downward, a protocol that has appreciated significantly (e.g., from 1.0 ETH to 1.5 ETH per rsETH) will have a proportionally larger absolute threshold. However, a slashing event that causes even a modest percentage drop from the current price can still exceed this threshold if the ATH is much higher than the current price.

`updateRSETHPrice()` is unrestricted — any address can call it:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

When the auto-pause fires, `LRTWithdrawalManager` is paused. Both `completeWithdrawal` and `unlockQueue` carry `whenNotPaused`:

```solidity
function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
``` [3](#0-2) 

```solidity
function unlockQueue(...) external nonReentrant onlySupportedAsset(asset) whenNotPaused onlyAssetTransferOrOperatorRole
``` [4](#0-3) 

Users who have already called `initiateWithdrawal` — transferring their rsETH into the withdrawal manager — cannot complete their withdrawals until an admin manually unpauses. Their rsETH is locked in the contract with no trustless recovery path during the pause.

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
``` [5](#0-4) 

The oracle itself is also paused, blocking `updateRSETHPrice()` (which has `whenNotPaused`) from being called again until admin intervention:

```solidity
modifier whenNotPaused() {
    if (paused) revert ContractPaused();
    _;
}
``` [6](#0-5) 

---

### Impact Explanation

**Temporary freezing of funds (Medium).** Users who have initiated withdrawals — transferring rsETH to `LRTWithdrawalManager` — cannot complete them while the protocol is paused. The rsETH is held in the contract with no trustless exit. Recovery requires admin (`onlyLRTAdmin`) to call `unpause()` on the withdrawal manager and oracle. If governance processes are slow or the admin is unavailable, the freeze is extended. This defeats the trustless nature of the withdrawal system.

---

### Likelihood Explanation

EigenLayer operator slashing is an explicitly documented and known risk of restaking. The `SlashingLib.sol` library included in this repository confirms that slashing is a first-class concept in the EigenLayer integration:

```solidity
function calcSlashedAmount(
    uint256 operatorShares,
    uint256 prevMaxMagnitude,
    uint256 newMaxMagnitude
) internal pure returns (uint256) {
    return operatorShares - operatorShares.mulDiv(newMaxMagnitude, prevMaxMagnitude, Math.Rounding.Up);
}
``` [7](#0-6) 

When slashing reduces the ETH value of assets held by `NodeDelegator`, `_getTotalEthInProtocol()` returns a lower value, which lowers `newRsETHPrice`. If this drop exceeds `pricePercentageLimit` from `highestRsethPrice`, the auto-pause fires. Since `updateRSETHPrice()` is public, any user or keeper can trigger this path immediately after slashing is reflected on-chain.

---

### Recommendation

The downside protection should distinguish between legitimate, verified loss scenarios (EigenLayer slashing) and anomalous price drops. Options include:

1. **Separate the downside threshold from the upside threshold** — use a dedicated, larger `maxDownsidePercentage` that is calibrated to realistic slashing magnitudes rather than reusing `pricePercentageLimit`.
2. **Compare against the previous price, not the all-time high** — the current design means a protocol that has grown 50% will pause on a 1% drop from ATH even if the price has been stable for months.
3. **Allow the price to update without pausing** — emit an event and let governance decide whether to pause, rather than auto-pausing on every price update that crosses the threshold.

---

### Proof of Concept

1. Protocol launches; rsETH price grows from 1.0 ETH to 1.5 ETH over 6 months. `highestRsethPrice = 1.5e18`.
2. Admin sets `pricePercentageLimit = 1e16` (1%).
3. Downside threshold = `1.5e18 × 1% = 0.015e18`.
4. An EigenLayer operator is slashed; the ETH value of restaked assets drops by 1.5% (a realistic slashing magnitude). New rsETH price = `1.5e18 × 0.985 = 1.4775e18`. Diff = `0.0225e18 > 0.015e18`.
5. Any address calls `updateRSETHPrice()`.
6. `_updateRsETHPrice()` detects `isPriceDecreaseOffLimit = true`, pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`.
7. All users with pending withdrawal requests (rsETH already locked in `LRTWithdrawalManager`) cannot call `completeWithdrawal` or have their requests unlocked via `unlockQueue`.
8. Funds remain frozen until admin calls `unpause()` on each contract.

### Citations

**File:** contracts/LRTOracle.sol (L47-50)
```text
    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }
```

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

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L268-281)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
```

**File:** contracts/external/eigenlayer/libraries/SlashingLib.sol (L189-200)
```text
    function calcSlashedAmount(
        uint256 operatorShares,
        uint256 prevMaxMagnitude,
        uint256 newMaxMagnitude
    )
        internal
        pure
        returns (uint256)
    {
        // round up mulDiv so we don't overslash
        return operatorShares - operatorShares.mulDiv(newMaxMagnitude, prevMaxMagnitude, Math.Rounding.Up);
    }
```
