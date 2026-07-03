### Title
Stale `rsETHPrice` After Price-Increase Threshold Revert Allows Depositors to Mint Excess rsETH — (File: `contracts/LRTOracle.sol`)

---

### Summary

When `LRTOracle.updateRSETHPrice()` is called by a non-manager and the computed new price exceeds `highestRsethPrice` by more than `pricePercentageLimit`, the function **reverts** without updating `rsETHPrice` and **without pausing deposits**. The stored `rsETHPrice` therefore remains stale at the old, lower value. Because `LRTDepositPool.getRsETHAmountToMint()` divides by the stored `rsETHPrice`, any depositor who acts during this window receives more rsETH than the current TVL justifies, diluting all existing rsETH holders.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` contains a price-increase guard:

```solidity
if (newRsETHPrice > highestRsethPrice) {
    uint256 priceDifference = newRsETHPrice - highestRsethPrice;
    bool isPriceIncreaseOffLimit =
        pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceIncreaseOffLimit) {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
            revert PriceAboveDailyThreshold();
        }
    }
}
``` [1](#0-0) 

When this branch reverts, execution never reaches the assignment `rsETHPrice = newRsETHPrice` at the bottom of the function: [2](#0-1) 

Critically, unlike the price-**decrease** branch (which calls `_pause()` on the deposit pool and withdrawal manager before returning), the price-**increase** branch issues no pause: [3](#0-2) 

`LRTDepositPool` remains open. Its mint calculation reads the now-stale `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

Because `rsETHPrice` is lower than the true current price, the denominator is too small and every depositor receives more rsETH than the protocol's TVL supports.

---

### Impact Explanation

**High — Theft of unclaimed yield / dilution of existing rsETH holders.**

Existing rsETH holders' proportional claim on the protocol's TVL is reduced by every deposit that occurs while `rsETHPrice` is stale. The attacker's rsETH is minted at the old (cheaper) rate; once a manager eventually calls `updateRSETHPriceAsManager()` and the price is corrected upward, the attacker's tokens are immediately worth more ETH than was deposited, at the expense of prior holders.

Concrete example:
- `rsETHPrice` = 1.00 ETH, true price = 1.02 ETH (2 % reward accrual).
- `pricePercentageLimit` = 1 % → public `updateRSETHPrice()` reverts; deposits remain open.
- Attacker deposits 100 ETH of stETH → receives `100 / 1.00` = 100 rsETH instead of `100 / 1.02` ≈ 98.04 rsETH.
- After manager corrects the price to 1.02 ETH, attacker's 100 rsETH is worth 102 ETH — a 2 ETH gain extracted from existing holders.

---

### Likelihood Explanation

**Medium.**

- `updateRSETHPrice()` is public and permissionless; any actor can trigger the revert condition.
- The condition fires whenever accumulated rewards push the price above the configured daily threshold — a routine occurrence in a live restaking protocol.
- The window between the revert and a manager's corrective call (`updateRSETHPriceAsManager()`) can span hours, during which the deposit pool is fully open.
- No special permissions or capital are required beyond a normal deposit.

---

### Recommendation

Mirror the price-decrease branch's behavior for the price-increase case: when `isPriceIncreaseOffLimit` is true and the caller is not a manager, **pause the deposit pool** (and optionally the withdrawal manager) before reverting, so that no deposits can occur while `rsETHPrice` is stale. Alternatively, allow the price update to proceed but cap it at `highestRsethPrice * (1 + pricePercentageLimit)` so that `rsETHPrice` is never left stale while deposits remain open.

---

### Proof of Concept

1. Protocol state: `rsETHPrice = 1.00e18`, `highestRsethPrice = 1.00e18`, `pricePercentageLimit = 1e16` (1 %).
2. Staking rewards accrue; `_getTotalEthInProtocol()` now returns a value that implies `newRsETHPrice = 1.02e18` (2 % increase).
3. Anyone calls `LRTOracle.updateRSETHPrice()`. Inside `_updateRsETHPrice()`, `isPriceIncreaseOffLimit = true` and the caller lacks `MANAGER` role → `revert PriceAboveDailyThreshold()`. `rsETHPrice` remains `1.00e18`. No pause is issued.
4. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
5. `getRsETHAmountToMint` computes `(100e18 * 1e18) / 1.00e18 = 100e18` rsETH. True fair amount is `(100e18 * 1e18) / 1.02e18 ≈ 98.04e18` rsETH. Attacker receives ~1.96 rsETH in excess.
6. Manager calls `updateRSETHPriceAsManager()`. `rsETHPrice` is updated to `1.02e18`. Attacker's 100 rsETH is now redeemable for 102 ETH — 2 ETH extracted from prior holders. [1](#0-0) [2](#0-1) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L252-267)
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
        }
```

**File:** contracts/LRTOracle.sol (L270-281)
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
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
