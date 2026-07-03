### Title
Unrestricted `sendFunds()` Allows Anyone to Force MEV Reward Distribution and Trigger Protocol Fee Minting - (File: contracts/FeeReceiver.sol)

### Summary
`FeeReceiver.sendFunds()` has no access control modifier, allowing any external caller to push the entire ETH balance of `FeeReceiver` into the deposit pool at any time. Because `LRTOracle.updateRSETHPrice()` is also unrestricted (`public whenNotPaused`), any caller can chain these two calls to force protocol fee minting (rsETH dilution of holders) at an arbitrary time, bypassing the operator's intended reward distribution schedule.

### Finding Description
`FeeReceiver` is an `AccessControlUpgradeable` contract that receives MEV and execution-layer rewards. Its `setDepositPool` admin function is correctly gated with `onlyRole(LRTConstants.MANAGER)`, but the primary operational function `sendFunds()` carries no access control at all:

```solidity
// contracts/FeeReceiver.sol:53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

This sends the entire ETH balance to the deposit pool, increasing `totalETHInProtocol`. `LRTOracle.updateRSETHPrice()` is also callable by anyone:

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

Inside `_updateRsETHPrice()`, when `totalETHInProtocol > previousTVL`, the protocol mints rsETH as a fee to the treasury:

```solidity
protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
...
IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
``` [3](#0-2) 

### Impact Explanation
Any unprivileged caller can:
1. Call `FeeReceiver.sendFunds()` to flush accumulated MEV/EL rewards into the deposit pool.
2. Call `LRTOracle.updateRSETHPrice()` to trigger protocol fee minting against that yield.

This forces fee minting at an attacker-chosen moment, bypassing the operator's intended batching or timing strategy. The minted rsETH dilutes existing holders' share of TVL. The protocol operator loses control over when fees are extracted from yield, and rsETH holders receive less yield than they would if the operator controlled the timing (e.g., to batch rewards or avoid fee minting during specific windows). This matches the **Low** impact category: the contract fails to deliver promised returns (full yield to holders) without losing principal value.

### Likelihood Explanation
The entry path is trivially reachable — both functions are `external`/`public` with no role check. Any EOA or contract can call them at any time the oracle is not paused. No special setup or capital is required.

### Recommendation
Add an access control modifier to `sendFunds()` restricting it to the `MANAGER` role (consistent with the rest of the contract's privileged functions):

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

### Proof of Concept
```
// Attacker EOA, no special role needed:
FeeReceiver(feeReceiver).sendFunds();       // flushes all MEV ETH into deposit pool
LRTOracle(lrtOracle).updateRSETHPrice();   // triggers protocol fee mint to treasury
// rsETH holders are diluted; attacker paid only gas
``` [1](#0-0) [4](#0-3) [3](#0-2)

### Citations

**File:** contracts/FeeReceiver.sol (L52-58)
```text
    /// @dev send all rewards to deposit pool
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTOracle.sol (L85-89)
```text
    /// @notice updates RSETH/ETH exchange rate
    /// @dev calculates rsETH price based on stakedAsset value received from EigenLayer
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L244-307)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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
        }

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

            // if price has decreased compared to the previous price, emit an event to reflect that
            if (previousPrice > newRsETHPrice) {
                emit RsETHPriceDecrease(newRsETHPrice, previousPrice);
            }

            // emit an event to notify that the price is currently below the peak (all time high) price
            emit RsETHPriceBelowPeak(highestRsethPrice, newRsETHPrice);
        }

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }

        // mint protocol fee as rsETH if there's a fee to take
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
```
