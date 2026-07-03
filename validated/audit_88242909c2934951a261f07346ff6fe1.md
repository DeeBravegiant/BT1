### Title
Lack of Slippage Protection in `instantWithdrawal` Allows Users to Receive Fewer Assets Than Expected - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal` burns a user-specified amount of rsETH and returns assets calculated from the oracle price at execution time, but provides no `minAssetAmountExpected` parameter. A user who previews the exchange rate off-chain via `getExpectedAssetAmount` and then submits the transaction has no on-chain guarantee that the oracle price will not have moved unfavorably by the time the transaction is mined, causing them to receive fewer assets than anticipated for the rsETH they burned.

---

### Finding Description

`instantWithdrawal` in `LRTWithdrawalManager.sol` accepts `rsETHUnstaked` (the amount of rsETH to burn) and computes the asset payout entirely from the live oracle price at execution time:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    string calldata referralId
) external nonReentrant whenNotPaused ...
{
    ...
    uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
    IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
    ...
    uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
    uint256 userAmount = assetAmountUnlocked - fee;
    ...
    _transferAsset(asset, msg.sender, userAmount);
``` [1](#0-0) 

`getExpectedAssetAmount` computes the payout as:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [2](#0-1) 

There is no `minAssetAmountExpected` parameter in the function signature. The user's rsETH is burned unconditionally before any check on the received asset amount. [3](#0-2) 

The same pattern applies to `initiateWithdrawal`, which also lacks a minimum asset amount guard: [4](#0-3) 

Compare this to `depositETH` / `depositAsset` in `LRTDepositPool`, which correctly enforce a `minRSETHAmountExpected` slippage guard: [5](#0-4) 

The deposit path is protected; the instant-withdrawal path is not.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but does not lose value.**

A user who queries `getExpectedAssetAmount` off-chain and then submits `instantWithdrawal` has their rsETH burned atomically. If the oracle price for rsETH or the target asset moves between query time and execution time (e.g., due to a Chainlink heartbeat update landing in the same block, or natural price drift during mempool delay), the user receives fewer assets than they anticipated. The rsETH is already burned and cannot be recovered.

---

### Likelihood Explanation

Oracle prices for rsETH and LSTs (stETH, ETHx) update on a regular heartbeat (typically every hour or on a 0.5 % deviation threshold). Any transaction that sits in the mempool across an oracle update, or that is submitted during a period of price movement, will silently deliver a worse-than-expected exchange rate. No privileged actor is required; this is triggered by any unprivileged user calling `instantWithdrawal` under normal market conditions.

---

### Recommendation

Add a `minAssetAmountExpected` parameter to `instantWithdrawal` (and `initiateWithdrawal`) and revert if the computed payout falls below it:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    uint256 minAssetAmountExpected,   // <-- add this
    string calldata referralId
) external ... {
    uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
    if (assetAmountUnlocked < minAssetAmountExpected) revert SlippageExceeded();
    IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
    ...
}
```

This mirrors the existing slippage guard already present in `LRTDepositPool._beforeDeposit`.

---

### Proof of Concept

1. Alice queries `getExpectedAssetAmount(stETH, 1 ether rsETH)` off-chain and sees she will receive `1.05 stETH`.
2. Alice submits `instantWithdrawal(stETH, 1 ether, "ref")`.
3. Before Alice's transaction is mined, a Chainlink oracle update raises the stETH/ETH price, reducing the rsETH-to-stETH ratio.
4. `getExpectedAssetAmount` now returns `1.02 stETH`.
5. Alice's 1 rsETH is burned and she receives only `1.02 stETH` (minus fee) — 0.03 stETH less than she expected — with no on-chain protection to revert the transaction.

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

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```
