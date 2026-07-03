### Title
Lack of access control on `updateRSETHPrice()` allows anyone to trigger protocol-wide pause — (`contracts/LRTOracle.sol`)

### Summary
`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard and no role-based access control. Any external caller can invoke it at will, triggering the internal price-update logic that — when the rsETH price has dropped beyond `pricePercentageLimit` — unconditionally pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself, freezing all user deposits and withdrawals.

### Finding Description
`updateRSETHPrice()` is the permissionless entry point into `_updateRsETHPrice()`:

```solidity
// LRTOracle.sol line 87
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

Inside `_updateRsETHPrice()`, when the newly computed price has fallen below `highestRsethPrice` by more than `pricePercentageLimit`, the function pauses all three core contracts with no caller check:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
``` [2](#0-1) 

The privileged counterpart `updateRSETHPriceAsManager()` is correctly gated:

```solidity
function updateRSETHPriceAsManager() external onlyLRTManager {
    _updateRsETHPrice();
}
``` [3](#0-2) 

The absence of any equivalent role modifier on `updateRSETHPrice()` means the pause path is reachable by any EOA or contract. The `pricePercentageLimit` and `highestRsethPrice` state variables that govern the threshold are set by the admin and are publicly readable, so an attacker can monitor them off-chain and call `updateRSETHPrice()` the moment the on-chain price satisfies the pause condition — before the protocol team has a chance to react.

Additionally, `updateRSETHPrice()` mints protocol-fee rsETH to the treasury whenever `totalETHInProtocol > previousTVL`:

```solidity
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
``` [4](#0-3) 

Anyone can race to call `updateRSETHPrice()` immediately after a reward accrual event, exhausting the `maxFeeMintAmountPerDay` daily cap and preventing the protocol's own keeper from recording the fee update for the rest of the day. [5](#0-4) 

### Impact Explanation
**Medium — Temporary freezing of funds.**

When the pause is triggered, `LRTDepositPool` and `LRTWithdrawalManager` are both paused. Users cannot deposit ETH/LSTs and cannot initiate or complete withdrawals until an admin calls `unpause()` on each contract. The pause is not self-reversing; it requires explicit admin action. During the freeze window all user funds are inaccessible.

A secondary impact is **permanent freezing of unclaimed yield** (Medium): exhausting `maxFeeMintAmountPerDay` via repeated calls blocks the protocol's fee-minting mechanism for the remainder of the 24-hour window, permanently losing that day's fee revenue.

### Likelihood Explanation
**Medium.** The price-drop condition (`newRsETHPrice < highestRsethPrice` by more than `pricePercentageLimit`) is a normal market event for a liquid-staking token. An attacker needs only to monitor the public state variables and submit a single transaction at the right moment — no capital, no privileged access, and no complex setup required. The attack is cheap and repeatable.

### Recommendation
Add `onlyLRTManager` (or a dedicated `KEEPER_ROLE`) to `updateRSETHPrice()`, matching the access pattern already applied to `updateRSETHPriceAsManager()`:

```solidity
// Before
function updateRSETHPrice() public whenNotPaused {

// After
function updateRSETHPrice() external whenNotPaused onlyLRTManager {
```

If a permissionless price-update path is intentionally desired (e.g., for keeper bots), the pause-triggering logic should be separated into a privileged function so that the public entry point cannot cause a protocol-wide freeze.

### Proof of Concept
1. Admin sets `pricePercentageLimit = 1e16` (1 %) and `highestRsethPrice` is recorded at `1.05 ether`.
2. A slashing event or oracle movement causes `_getTotalEthInProtocol()` to return a value such that `newRsETHPrice = 1.03 ether` — a 1.9 % drop, exceeding the 1 % limit.
3. Attacker calls `LRTOracle.updateRSETHPrice()` (no role required, contract not paused).
4. `_updateRsETHPrice()` computes `isPriceDecreaseOffLimit = true` and executes:
   - `lrtDepositPool.pause()` — all deposits blocked.
   - `withdrawalManager.pause()` — all withdrawals blocked.
   - `_pause()` — oracle itself paused.
5. All user funds are frozen until the admin manually unpauses each contract. [1](#0-0) [6](#0-5)

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

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
    }
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
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
