### Title
rsETH Holders Can Withdraw Any Supported LST Collateral Regardless of What They Deposited, Leaving Original Depositors Unable to Recover Their Asset — (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

The LRT-rsETH protocol accepts multiple LST assets (stETH, ETHx, sfrxETH, ETH) to mint a single fungible rsETH token. Any rsETH holder can initiate a withdrawal for **any** supported asset, not just the one they originally deposited. This means a user who deposited stETH can withdraw ETHx, depleting the ETHx reserve and leaving ETHx depositors unable to recover their original collateral. In a depeg or exploit scenario affecting one LST, depositors of that LST can race to drain the good collateral, socializing losses onto depositors of the healthy LST.

---

### Finding Description

`LRTWithdrawalManager.initiateWithdrawal()` accepts an arbitrary `asset` parameter from the caller:

```solidity
function initiateWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    string calldata referralId
)
    external
    nonReentrant
    whenNotPaused
    onlySupportedAsset(asset)
    onlySupportedStrategy(asset)
``` [1](#0-0) 

The only constraint is that `asset` must be a supported asset — there is no check that the caller ever deposited that specific asset. The expected withdrawal amount is computed purely from the current rsETH price and the asset's oracle price:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [2](#0-1) 

The available-asset guard only prevents withdrawing more than the total deposited amount of that asset minus already-committed amounts:

```solidity
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
``` [3](#0-2) 

This guard prevents over-withdrawal of a single asset but does **not** prevent cross-collateral substitution. A user who deposited stETH can freely request ETHx, and vice versa.

The rsETH price itself is computed by aggregating all supported assets' ETH values:

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [4](#0-3) 

This means rsETH's value is backed by the **aggregate** of all collateral, and any rsETH holder has an implicit claim on any individual collateral pool.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but does not directly lose value at current oracle prices.**

A user who deposited stETH and holds rsETH can call `initiateWithdrawal(ETHx, rsETHAmount)`, draining the ETHx reserve. An ETHx depositor who later calls `initiateWithdrawal(ETHx, ...)` will receive `ExceedAmountToWithdraw` and be forced to withdraw stETH instead. If stETH subsequently depegs, that user suffers a loss that was caused by another user's cross-collateral withdrawal — a loss they could not have anticipated or prevented. The protocol's effective value floor for any individual depositor is the value of the **weakest** collateral in the basket, not the collateral they deposited.

---

### Likelihood Explanation

**Medium.** This is a structural property of the protocol that is always present whenever more than one LST is supported. No special conditions are required: any rsETH holder can exercise cross-collateral withdrawal at any time. The risk materialises most severely during a depeg or exploit of one LST, which is a realistic scenario given the protocol's multi-collateral design.

---

### Recommendation

1. **Document and communicate** clearly to users that rsETH does not guarantee return of the originally deposited LST. The protocol's value guarantee is the ETH-equivalent value of the basket, not any specific asset.
2. **Consider per-asset withdrawal attribution**: track which asset each rsETH tranche was minted against and restrict withdrawals to that asset, at the cost of fungibility.
3. **Implement a redemption priority queue** (as noted in the external report's analogous system) that returns a proportional basket of all collateral assets on withdrawal, rather than allowing full selection of a single asset.
4. **Ensure `pricePercentageLimit` is always set** so that the auto-pause in `_updateRsETHPrice()` triggers promptly if any collateral depegs, limiting the window for cross-collateral drain. [5](#0-4) 

---

### Proof of Concept

```
1. Protocol supports stETH and ETHx. Both are at 1:1 ETH peg.
2. Alice deposits 100 stETH → receives rsETH proportional to 100 ETH value.
3. Bob deposits 100 ETHx → receives rsETH proportional to 100 ETH value.
4. Alice calls initiateWithdrawal(ETHx, aliceRsETH).
   - getAvailableAssetAmount(ETHx) = 100 ETHx (Bob's deposit), passes check.
   - assetsCommitted[ETHx] += 100 ETHx.
5. Bob calls initiateWithdrawal(ETHx, bobRsETH).
   - getAvailableAssetAmount(ETHx) = 0 → reverts with ExceedAmountToWithdraw.
6. Bob is forced to call initiateWithdrawal(stETH, bobRsETH) instead.
7. If stETH subsequently depegs, Bob receives devalued stETH while Alice holds Bob's ETHx.
```

The entry path is fully unprivileged: `LRTWithdrawalManager.initiateWithdrawal()` is callable by any rsETH holder with no role requirement. [6](#0-5)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
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

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
