### Title
Publicly Callable `updateRSETHPrice()` Carries Admin-Level Protocol Pause Power — (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function that internally executes `_updateRsETHPrice()`, which can pause `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself — all admin-level actions — when the computed rsETH price drops below the configured `pricePercentageLimit` threshold. Any unprivileged external caller can trigger this protocol-wide pause.

### Finding Description
`updateRSETHPrice()` carries no access-control modifier:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

Inside `_updateRsETHPrice()`, when the newly computed price falls below `highestRsethPrice` by more than `pricePercentageLimit`, the function directly calls `.pause()` on `LRTDepositPool`, `LRTWithdrawalManager`, and itself:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
``` [2](#0-1) 

These pause calls are admin-level operations — normally gated behind `PAUSER_ROLE` or `DEFAULT_ADMIN_ROLE` in each contract — yet they are reachable by any EOA through the public `updateRSETHPrice()` entry point. Additionally, the same function mints rsETH to the treasury as protocol fees, another privileged action:

```solidity
IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
``` [3](#0-2) 

The analog to the Redpanda report is direct: a component exposed to the public (`updateRSETHPrice()`) holds admin-level power (pausing the entire protocol, minting tokens) that should be restricted to privileged roles.

### Impact Explanation
When the price threshold condition is met (e.g., after a validator slashing event or a significant LST depeg), any unprivileged caller can invoke `updateRSETHPrice()` to atomically pause `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`. This freezes all user deposits and all withdrawal completions until an admin manually unpauses each contract. Users with pending withdrawals past their delay window cannot claim funds; depositors cannot enter the protocol. This constitutes **temporary freezing of funds** (Medium impact per scope).

### Likelihood Explanation
The condition requires `newRsETHPrice < highestRsethPrice` by more than `pricePercentageLimit`. This is realistic during:
- EigenLayer validator slashing events that reduce the ETH backing
- A significant LST depeg that lowers the oracle-reported asset value

In such scenarios the price condition is met on-chain and any watcher (including a griefing attacker) can race to call `updateRSETHPrice()` before the protocol team can respond, locking users out of withdrawals during a period when they most need liquidity.

### Recommendation
- Gate `updateRSETHPrice()` with at minimum an `onlyLRTOperator` or `onlyLRTManager` modifier, matching the privilege level of the actions it can trigger.
- Alternatively, decouple the price-update logic from the auto-pause logic: let the price update remain public, but require a privileged role to execute the pause branch.
- The existing `updateRSETHPriceAsManager()` already demonstrates the intended privileged path; the public variant should not duplicate its most dangerous side-effects. [4](#0-3) 

### Proof of Concept
1. A slashing event reduces the ETH value backing rsETH such that `newRsETHPrice < highestRsethPrice * (1 - pricePercentageLimit)`.
2. Attacker (any EOA) calls `LRTOracle.updateRSETHPrice()`.
3. `_updateRsETHPrice()` computes the new price, detects `isPriceDecreaseOffLimit == true`, and calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`.
4. All user deposits and withdrawal completions are frozen. Users with matured withdrawal requests cannot claim their LSTs/ETH until an admin unpauses each contract individually.
5. The attacker spent only gas; no capital was required. [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L306-307)
```text
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
```
