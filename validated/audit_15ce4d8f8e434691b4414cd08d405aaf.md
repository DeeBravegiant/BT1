### Title
Permissionless `sendFunds()` and `updateRSETHPrice()` Enable Theft of Unclaimed MEV Yield - (File: contracts/FeeReceiver.sol, contracts/LRTOracle.sol)

### Summary
`FeeReceiver.sendFunds()` carries no access-control modifier, and `LRTOracle.updateRSETHPrice()` carries no role check. Any unprivileged caller can atomically force accumulated MEV/execution-layer rewards into the deposit-pool TVL and then trigger a price update, capturing yield that was earned by pre-existing rsETH holders before the attacker entered the position.

### Finding Description
`FeeReceiver.sendFunds()` is declared `external` with no modifier:

```solidity
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

`LRTOracle.updateRSETHPrice()` is declared `public whenNotPaused` with no role check:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

`_updateRsETHPrice()` computes the new rsETH price as `(totalETHInProtocol − protocolFeeInETH) / rsethSupply`. The moment `sendFunds()` is called, `totalETHInProtocol` jumps by the entire accumulated MEV balance. The subsequent `updateRSETHPrice()` call locks in that higher price and mints protocol fees to the treasury. [3](#0-2) 

The three `receive*` helpers on `LRTDepositPool` are also unrestricted `external payable` stubs, meaning any address can inject ETH into the TVL accounting through them as well:

```solidity
function receiveFromRewardReceiver() external payable { }
function receiveFromLRTConverter()   external payable { }
function receiveFromNodeDelegator()  external payable { }
``` [4](#0-3) 

### Impact Explanation
**High — Theft of unclaimed yield.**

MEV rewards sitting in `FeeReceiver` are "unclaimed yield" owed proportionally to rsETH holders who were present while those rewards accrued. An attacker who acquires rsETH *after* the rewards were earned and then immediately calls `sendFunds()` + `updateRSETHPrice()` dilutes the original holders' share:

- Suppose 100 rsETH outstanding, 10 ETH MEV in `FeeReceiver`.
- Attacker buys 10 rsETH → 110 rsETH total.
- Attacker calls `sendFunds()` → 10 ETH enters TVL.
- Attacker calls `updateRSETHPrice()` → price rises; attacker's 10 rsETH now represents 10/110 × 10 ETH ≈ 0.91 ETH of the MEV yield.
- Original holders receive only 100/110 × 10 ETH ≈ 9.09 ETH instead of the full 10 ETH they earned.

The attacker then requests withdrawal at the elevated price (rsETH is burned at the withdrawal-request price per `LRTWithdrawalManager`), waits the `withdrawalDelayBlocks` (~8 days), and claims the asset. [5](#0-4) 

### Likelihood Explanation
**Medium.** The attack requires no special privilege — only the ability to acquire rsETH on the open market and call two permissionless functions. MEV rewards accumulate continuously, so the opportunity recurs every block. The 8-day withdrawal delay introduces price risk but does not prevent the attack; the withdrawal amount is fixed at the time of the request, so the attacker locks in the inflated price immediately. The `pricePercentageLimit` guard in `_updateRsETHPrice()` caps the per-call price increase but does not prevent repeated calls across multiple periods. [6](#0-5) 

### Recommendation
1. Add an access-control modifier to `FeeReceiver.sendFunds()` (e.g., `onlyRole(LRTConstants.MANAGER)`) so only authorized protocol actors can trigger MEV reward distribution.
2. Consider restricting `LRTOracle.updateRSETHPrice()` to `onlyLRTOperator` or a keeper role, or at minimum add a time-lock between successive public calls.
3. Add caller validation to `LRTDepositPool.receiveFromRewardReceiver()`, `receiveFromLRTConverter()`, and `receiveFromNodeDelegator()` so only the corresponding protocol contracts can invoke them.

### Proof of Concept
```
1. Attacker observes N ETH accumulated in FeeReceiver.
2. Attacker acquires rsETH on a DEX (flash-loan or spot).
3. Attacker calls FeeReceiver.sendFunds()
   → N ETH transferred to LRTDepositPool via receiveFromRewardReceiver().
4. Attacker calls LRTOracle.updateRSETHPrice()
   → rsETHPrice increases; attacker's rsETH is now worth more.
5. Attacker calls LRTWithdrawalManager.initiateWithdrawal(...)
   → withdrawal amount locked at the elevated price.
6. After withdrawalDelayBlocks (~8 days), attacker claims ETH/LST.
7. Net gain: attacker's proportional share of N ETH MEV rewards
   that were earned entirely by pre-existing rsETH holders.
```

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L244-251)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTDepositPool.sol (L61-67)
```text
    function receiveFromRewardReceiver() external payable { }

    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }

    /// @dev receive from NodeDelegator
    function receiveFromNodeDelegator() external payable { }
```

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```
