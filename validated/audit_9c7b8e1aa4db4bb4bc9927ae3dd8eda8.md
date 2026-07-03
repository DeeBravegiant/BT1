### Title
No Minimum Output Check After Fee Deduction in `instantWithdrawal` Allows Users to Receive Less Assets Than Expected - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager::instantWithdrawal` computes `assetAmountUnlocked` from the oracle at execution time, then deducts `instantWithdrawalFee` to produce `userAmount`. There is no `minAssetAmountExpected` parameter and no post-fee minimum output check. This is a direct analog of the "MinTokensOut For Intermediate, Not Final Amount" vulnerability class: the user burns rsETH irreversibly but has no slippage protection on the final asset amount received.

### Finding Description
In `instantWithdrawal`, the execution flow is:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked); // irreversible

// ...

uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;

_transferAsset(asset, msg.sender, userAmount);
```

`getExpectedAssetAmount` computes:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

Both oracle prices are read at execution time. The function signature accepts only `(address asset, uint256 rsETHUnstaked, string calldata referralId)` — there is no `minAssetAmountExpected` parameter. The code's own NatSpec comment acknowledges the risk: *"Uses the fee set at execution time. Managers can raise it right before this call, making withdrawals cost more than expected."*

The rsETH is burned before the fee is deducted, so the user cannot abort after seeing the final `userAmount`.

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who previews the withdrawal via `getExpectedAssetAmount` and then calls `instantWithdrawal` may receive materially fewer assets than expected because:
1. The oracle-derived `assetAmountUnlocked` can decrease between preview and execution due to normal market price movement of `rsETHPrice` or `getAssetPrice(asset)`.
2. The `instantWithdrawalFee` (up to 10%, capped at 1000 bps) is deducted after the oracle calculation with no floor check on `userAmount`.

The user has burned rsETH irreversibly and has no on-chain mechanism to enforce a minimum acceptable output.

### Likelihood Explanation
**Medium.** Oracle prices for rsETH and LST assets update continuously. Any block-level price movement between a user's off-chain preview and on-chain execution silently reduces `userAmount` below the previewed figure. No adversarial action is required — this is ordinary market behavior. The absence of a `minAssetAmountExpected` guard means every `instantWithdrawal` call is exposed to this slippage with no protection.

### Recommendation
Add a `minAssetAmountExpected` parameter to `instantWithdrawal` and enforce it after fee deduction:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    uint256 minAssetAmountExpected,  // <-- add this
    string calldata referralId
) external ... {
    ...
    uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
    uint256 userAmount = assetAmountUnlocked - fee;

    if (userAmount < minAssetAmountExpected) revert SlippageExceeded();  // <-- add this

    _transferAsset(asset, msg.sender, userAmount);
}
```

This mirrors the pattern already used in `LRTDepositPool::depositETH` / `depositAsset` where `minRSETHAmountExpected` is checked after the full computation.

### Proof of Concept

1. rsETH/ETH oracle price = 1.05e18, stETH/ETH oracle price = 1.00e18.
2. User calls `getExpectedAssetAmount(stETH, 1e18 rsETH)` → returns `1.05e18 stETH`.
3. User decides to call `instantWithdrawal(stETH, 1e18, referralId)` expecting ~1.05 stETH minus fee.
4. Before the tx lands, oracle updates: rsETH/ETH drops to 1.00e18.
5. `assetAmountUnlocked = 1e18 * 1.00e18 / 1.00e18 = 1.00e18 stETH`.
6. `fee = 1.00e18 * 500 / 10_000 = 0.05e18` (5% fee).
7. `userAmount = 0.95e18 stETH` — user receives 9.5% less than previewed with no revert.
8. rsETH is already burned at step 5 (line 229); there is no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L372-374)
```text
    function setInstantWithdrawalFee(uint256 feeBasisPoints) external onlyLRTManager {
        if (feeBasisPoints > 1000) revert FeeTooHigh(); // Max 10%
        instantWithdrawalFee = feeBasisPoints;
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
