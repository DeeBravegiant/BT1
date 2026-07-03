Audit Report

## Title
Unprotected `sendFunds()` in `FeeReceiver` Enables Attacker-Timed Reward Flush and rsETH Price Manipulation — (File: contracts/FeeReceiver.sol)

## Summary
`FeeReceiver.sendFunds()` carries no access-control modifier, allowing any caller to move the entire accumulated MEV/execution-layer reward balance into `LRTDepositPool` at will. Because `LRTOracle._getTotalEthInProtocol()` counts `address(LRTDepositPool).balance` and `LRTOracle.updateRSETHPrice()` is also publicly callable, an attacker can deposit ETH at the stale oracle price, flush the pending rewards via `sendFunds()`, update the oracle price via the public `updateRSETHPrice()`, and exit on a secondary market at the inflated price — extracting yield earned by long-term rsETH holders.

## Finding Description
**Root cause — `FeeReceiver.sendFunds()` (line 53):**
```solidity
function sendFunds() external {          // ← no access control
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```
`receiveFromRewardReceiver()` in `LRTDepositPool` (line 61) is equally open:
```solidity
function receiveFromRewardReceiver() external payable { }
```
Once ETH lands in `LRTDepositPool`, `getETHDistributionData()` (line 480) immediately counts it:
```solidity
ethLyingInDepositPool = address(this).balance;
```
`LRTOracle._getTotalEthInProtocol()` (lines 331–349) calls `ILRTDepositPool.getTotalAssetDeposits(ETH)` → `getETHDistributionData()`, so the new balance is reflected in the next oracle price computation. Critically, `LRTOracle.updateRSETHPrice()` (line 87) is **public** with only a `whenNotPaused` guard:
```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```
`getRsETHAmountToMint()` (line 520) uses the **stored** `rsETHPrice` state variable, not a live TVL ratio:
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
The stored price is only updated when `updateRSETHPrice()` is called. This creates the exploitable window:

**Exploit path:**
1. Attacker deposits ETH at the current stale oracle price → receives rsETH priced before pending rewards are counted.
2. Attacker calls `FeeReceiver.sendFunds()` → pending MEV rewards move into `LRTDepositPool`, increasing `address(LRTDepositPool).balance`.
3. Attacker calls `LRTOracle.updateRSETHPrice()` (public) → stored `rsETHPrice` is recomputed upward to reflect the newly added ETH.
4. Attacker sells rsETH on a secondary market now priced at the higher oracle rate, capturing yield earned by pre-existing holders.

**Existing guards reviewed and found insufficient:**
- `pricePercentageLimit` in `_updateRsETHPrice()` (lines 252–267) can block a single large jump, but: (a) the limit may be 0 (unset), (b) the attacker can split the reward flush across multiple smaller `sendFunds()` + `updateRSETHPrice()` calls to stay within the threshold, and (c) the guard only applies when `newRsETHPrice > highestRsethPrice`, not for moderate increases.
- `nonReentrant` on `depositETH` prevents reentrancy but does not prevent the multi-transaction sequence above.
- No whitelist on `receiveFromRewardReceiver()` means any address can inject ETH and trigger the same price-update effect.

## Impact Explanation
**High — Theft of unclaimed yield.** MEV rewards accumulate in `FeeReceiver` over time and represent yield proportionally owed to all rsETH holders for their holding period. By depositing immediately before forcing the reward flush and oracle update, an attacker receives a disproportionate share of those rewards without having held rsETH during the accrual period. Existing holders' rsETH is diluted in relative yield terms; the attacker extracts the difference via secondary-market sale at the inflated oracle-tracked price.

## Likelihood Explanation
**Medium.** Prerequisites are: (1) a non-trivial ETH balance in `FeeReceiver` — routine after any validator proposal or MEV event; (2) `LRTOracle` not paused; (3) `pricePercentageLimit` either unset or the reward amount within the threshold. No privileged access, oracle operator compromise, or governance capture is required. The full sequence is three permissionless transactions executable by any EOA.

## Recommendation
1. Add an access-control guard to `sendFunds()` so only an authorized role can trigger reward distribution:
```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```
2. Restrict `LRTDepositPool.receiveFromRewardReceiver()` to only accept calls from the registered `FeeReceiver` address.
3. Consider restricting `LRTOracle.updateRSETHPrice()` to an authorized keeper role, or at minimum ensure `pricePercentageLimit` is always set to a non-zero value to bound single-block price jumps.

## Proof of Concept
Assume: `FeeReceiver` holds 100 ETH in accumulated MEV rewards; `LRTDepositPool` TVL = 1 000 ETH; rsETH supply = 1 000 rsETH; stored `rsETHPrice` = 1.000 ETH/rsETH; `pricePercentageLimit` = 0 or reward within threshold.

**Tx 1** — `LRTDepositPool.depositETH{value: 100 ETH}(minRSETH, "")`.
`getRsETHAmountToMint` uses stored price 1.000 → attacker receives **100 rsETH**.

**Tx 2** — `FeeReceiver.sendFunds()`.
100 ETH moves to `LRTDepositPool`. `address(LRTDepositPool).balance` increases by 100 ETH. Stored `rsETHPrice` is still 1.000 (oracle not yet updated).

**Tx 3** — `LRTOracle.updateRSETHPrice()` (public, no privilege needed).
`_getTotalEthInProtocol()` now sees 1 200 ETH total; rsETH supply = 1 100; new `rsETHPrice` ≈ **1.0909 ETH/rsETH** (minus any protocol fee). Stored price is updated.

**Tx 4** — Attacker sells 100 rsETH on secondary market now tracking oracle price ≈ 1.0909 → receives ≈ **109.09 ETH**.

**Net profit**: ≈ 9.09 ETH extracted from MEV rewards earned by the 1 000 pre-existing rsETH holders.

Foundry fork test outline:
```solidity
function testSendFundsRewardSniping() public fork {
    // fund FeeReceiver with 100 ETH (simulate MEV rewards)
    vm.deal(address(feeReceiver), 100 ether);
    uint256 priceBefore = lrtOracle.rsETHPrice();

    // attacker deposits at stale price
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 100 ether}(0, "");
    uint256 rsethReceived = rseth.balanceOf(attacker);

    // attacker flushes rewards
    vm.prank(attacker);
    feeReceiver.sendFunds();

    // attacker updates oracle price (public call)
    vm.prank(attacker);
    lrtOracle.updateRSETHPrice();

    uint256 priceAfter = lrtOracle.rsETHPrice();
    assertGt(priceAfter, priceBefore, "price should have increased");

    // attacker's rsETH is now worth more than deposited
    uint256 attackerValueETH = rsethReceived * priceAfter / 1e18;
    assertGt(attackerValueETH, 100 ether, "attacker profits from yield sniping");
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

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

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```
