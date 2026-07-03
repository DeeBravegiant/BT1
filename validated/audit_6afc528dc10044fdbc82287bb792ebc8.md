### Title
`LRTWithdrawalManager::instantWithdrawal` Burns rsETH Without Minimum Asset Output Protection — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`instantWithdrawal` burns a caller-specified amount of rsETH and returns assets computed from the live oracle price at execution time. No `minAssetAmountExpected` guard exists, so any oracle price movement between transaction submission and inclusion causes the user to permanently burn rsETH and receive fewer underlying assets than anticipated — an exact structural analog to the unbounded-slippage-on-burnt-tokens class described in the reference report.

---

### Finding Description

`instantWithdrawal` computes the asset payout with `getExpectedAssetAmount`, which reads `lrtOracle.rsETHPrice()` and `lrtOracle.getAssetPrice(asset)` at execution time: [1](#0-0) 

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

`getExpectedAssetAmount` is: [2](#0-1) 

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

The function signature accepts only `asset`, `rsETHUnstaked`, and `referralId`: [3](#0-2) 

There is no `minAssetAmountExpected` parameter. The user has no on-chain mechanism to bound the minimum assets they will receive in exchange for the rsETH they burn.

By contrast, the L1 deposit path (`LRTDepositPool.depositETH` / `depositAsset`) explicitly enforces a caller-supplied minimum: [4](#0-3) 

```solidity
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

`instantWithdrawal` is the only user-facing burn path that lacks an equivalent guard.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who previews the expected payout off-chain (e.g., via `getExpectedAssetAmount`) and then submits `instantWithdrawal` may receive materially fewer assets if the oracle price moves before the transaction is mined. The rsETH is permanently destroyed; the shortfall in assets remains in `LRTUnstakingVault`, accruing to remaining rsETH holders. The user suffers a one-sided loss with no recourse.

---

### Likelihood Explanation

**Low-Medium.** The rsETH oracle price is updated by managers via `LRTOracle._updateRsETHPrice`, which reads live TVL and supply: [5](#0-4) 

Price updates occur regularly (e.g., on reward accrual). A user who signs a transaction during a period of price movement — or whose transaction is delayed in the mempool — will silently receive fewer assets than the preview showed. No front-running or privileged action is required; ordinary oracle cadence is sufficient.

---

### Recommendation

Add a `minAssetAmountExpected` parameter to `instantWithdrawal` and revert if the computed payout falls below it:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    uint256 minAssetAmountExpected,   // <-- add
    string calldata referralId
) external ... {
    ...
    uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
    if (assetAmountUnlocked < minAssetAmountExpected) revert SlippageExceeded();
    IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
    ...
}
```

This mirrors the slippage guard already present in `LRTDepositPool._beforeDeposit`.

---

### Proof of Concept

1. User calls `getExpectedAssetAmount(stETH, 1e18)` off-chain and sees they will receive `1.05 stETH` for `1 rsETH`.
2. User submits `instantWithdrawal(stETH, 1e18, "ref")`.
3. Before inclusion, a manager calls `LRTOracle.updateRsETHPrice()` — rsETH price drops from `1.05e18` to `1.00e18` (e.g., due to a slashing event being reflected).
4. `getExpectedAssetAmount` now returns `1.00 stETH` instead of `1.05 stETH`.
5. The transaction executes: `1 rsETH` is permanently burned, user receives only `1.00 stETH` minus the instant-withdrawal fee — `0.05 stETH` less than previewed, with no revert and no recourse. [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L212-216)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
```

**File:** contracts/LRTWithdrawalManager.sol (L228-252)
```text
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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L214-220)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
```
