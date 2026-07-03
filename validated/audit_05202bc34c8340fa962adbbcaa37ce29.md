### Title
Missing Slippage Protection in `instantWithdrawal()` Exposes rsETH Burners to Oracle Rate Slippage — (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.instantWithdrawal()` burns rsETH from the caller and transfers underlying assets based on the oracle-derived rate at execution time, but accepts no `minAssetAmountOut` parameter. Because rsETH is burned before the asset transfer occurs, the action is irreversible and users have no on-chain mechanism to reject an unfavorable rate.

### Finding Description
`instantWithdrawal()` computes the asset amount to return via `getExpectedAssetAmount()`, which reads live oracle prices from `ILRTOracle`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

The function signature accepts only `asset`, `rsETHUnstaked`, and `referralId` — no minimum output bound:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    string calldata referralId
) external nonReentrant whenNotPaused ...
```

Execution order:
1. `assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked)` — oracle rate read
2. `IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked)` — rsETH destroyed
3. Fee deducted: `userAmount = assetAmountUnlocked - fee`
4. `_transferAsset(asset, msg.sender, userAmount)` — assets sent

If the oracle rate moves adversely between the user's transaction submission and its on-chain execution (e.g., due to LST price fluctuation, oracle update, or block reordering), the user receives fewer assets than anticipated with no ability to revert.

The code's own NatSpec acknowledges a related slippage vector: *"Uses the fee set at execution time. Managers can raise it right before this call, making withdrawals cost more than expected."* The oracle rate is an additional unguarded variable.

By contrast, the L1 deposit path in `LRTDepositPool` explicitly guards against this with a `minRSETHAmountExpected` parameter enforced in `_beforeDeposit`:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

No equivalent guard exists on the withdrawal side.

### Impact Explanation
A user who calls `instantWithdrawal()` during a period of oracle rate movement receives fewer underlying assets (ETH or LST) than the rate they observed off-chain. Because rsETH is burned atomically before the asset transfer, the loss is permanent. The user cannot cancel or retry at a better rate.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation
`instantWithdrawal()` is a public, permissionless function callable by any rsETH holder whenever `isInstantWithdrawalEnabled[asset]` is true. Oracle rates for rsETH and underlying LSTs update continuously. Any network congestion, mempool delay, or oracle update between transaction submission and inclusion can shift the rate. No special attacker capability is required; ordinary market conditions are sufficient.

### Recommendation
Add a `minAssetAmountOut` parameter to `instantWithdrawal()` and revert if the computed `userAmount` falls below it:

```solidity
function instantWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    uint256 minAssetAmountOut,   // <-- add
    string calldata referralId
) external ... {
    ...
    uint256 userAmount = assetAmountUnlocked - fee;
    if (userAmount < minAssetAmountOut) revert SlippageExceeded();
    ...
}
```

This mirrors the protection already present in `LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()`.

### Proof of Concept

1. rsETH/ETH oracle rate is 1.05 ETH per rsETH at block N.
2. User submits `instantWithdrawal(ETH, 10e18, "")` expecting ≈9.975 ETH (after 0.25% fee).
3. Before the transaction is included, an oracle update sets the rate to 1.00 ETH per rsETH.
4. Transaction executes at block N+k: `assetAmountUnlocked = 10e18 * 1.00 / 1.00 = 10 ETH`, fee deducted → `userAmount = 9.975 ETH`.
5. User receives 9.975 ETH instead of the ~10.4 ETH they expected at the rate they saw. rsETH is already burned; no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L648-669)
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
```
