### Title
Stale `rsETHPrice` in `instantWithdrawal` Allows Users to Exit at Pre-Loss Rate, Dumping Losses on Remaining Holders - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal` computes the asset payout using `LRTOracle.rsETHPrice`, a **cached state variable** that is only updated when `updateRSETHPrice()` is explicitly called. If a loss event occurs (e.g., EigenLayer slashing) before the oracle is refreshed, a user can call `instantWithdrawal` at the stale pre-loss rate, receiving more assets than they are entitled to. The unaccounted loss is then silently borne by all remaining rsETH holders.

---

### Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate in a state variable `rsETHPrice`: [1](#0-0) 

This value is only updated when `updateRSETHPrice()` is called — a permissionless function, but one that is **never automatically invoked** by any withdrawal path: [2](#0-1) 

`LRTWithdrawalManager.getExpectedAssetAmount` reads this cached value directly: [3](#0-2) 

`instantWithdrawal` calls `getExpectedAssetAmount` to determine how many assets to disburse, then immediately burns rsETH and redeems from the unstaking vault — all in a single transaction, with no oracle refresh: [4](#0-3) 

There is **no minimum-comparison guard** here (unlike the queued withdrawal path, which uses `_calculatePayoutAmount` to take the lesser of the locked-in amount and the current return at unlock time): [5](#0-4) 

The `instantWithdrawal` path skips this protection entirely, making it exploitable whenever `rsETHPrice` is stale.

---

### Impact Explanation

**High — Theft of funds from other rsETH holders.**

A user who exits via `instantWithdrawal` while `rsETHPrice` is stale (pre-loss) receives assets valued at the inflated pre-loss rate. The shortfall is not immediately realized; it is absorbed by all remaining rsETH holders when `updateRSETHPrice()` is eventually called and the price drops. This is a direct, quantifiable transfer of value from passive holders to the exiting user, matching the "fund theft / insolvency" class of the reference vulnerability.

---

### Likelihood Explanation

**Medium.**

Two conditions must hold simultaneously:
1. Instant withdrawal must be enabled for the target asset (`isInstantWithdrawalEnabled[asset] == true`).
2. A loss event (EigenLayer slashing, strategy insolvency) must have occurred but `updateRSETHPrice()` must not yet have been called.

Condition 1 is a protocol configuration choice that is already live for some assets. Condition 2 is a realistic race window: `updateRSETHPrice()` is not called atomically with loss events, and a sophisticated user monitoring EigenLayer state can act within the same block or the next few blocks before any keeper refreshes the oracle. The `pricePercentageLimit` downside-protection mechanism in `_updateRsETHPrice` only triggers a pause **after** the oracle is updated — it provides zero protection during the stale window. [6](#0-5) 

---

### Recommendation

Call `updateRSETHPrice()` (or an equivalent internal price refresh) at the start of `instantWithdrawal`, before computing `assetAmountUnlocked`. This ensures the payout is always based on the current, loss-inclusive exchange rate:

```solidity
function instantWithdrawal(address asset, uint256 rsETHUnstaked, string calldata referralId)
    external nonReentrant whenNotPaused ...
{
    // Refresh oracle before computing payout
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();

    uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
    ...
}
```

Alternatively, enforce a maximum staleness window on `rsETHPrice` and revert if the price has not been updated within that window.

---

### Proof of Concept

1. Protocol has 200 ETH of staked assets backing 200 rsETH (price = 1 ETH/rsETH). Instant withdrawal is enabled for ETH.
2. EigenLayer slashing reduces the staked ETH to 100 ETH. `rsETHPrice` in `LRTOracle` is still `1e18` (stale).
3. BOB holds 100 rsETH. BOB calls `instantWithdrawal(ETH, 100e18, "")`.
4. `getExpectedAssetAmount` computes `100e18 * 1e18 / 1e18 = 100 ETH` using the stale price.
5. BOB receives 100 ETH (minus fee) and his rsETH is burned.
6. ALICE holds 100 rsETH. When `updateRSETHPrice()` is called, `_getTotalEthInProtocol()` returns 0 ETH (all withdrawn), and ALICE's shares are worthless.
7. BOB can re-deposit 100 ETH and receive far more rsETH shares at the now-depressed price, profiting from the loss he avoided. [7](#0-6) [8](#0-7) [2](#0-1)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
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

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
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
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L824-834)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
