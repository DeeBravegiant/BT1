### Title
Stale `rsETHPrice` Used in Withdrawal Amount Calculation Without Prior Oracle Update - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.initiateWithdrawal` and `instantWithdrawal` compute `expectedAssetAmount` by calling `getExpectedAssetAmount`, which reads `lrtOracle.rsETHPrice()` — a cached storage variable in `LRTOracle`. Neither function calls `updateRSETHPrice()` before reading this value. This is the direct analog of the reported vulnerability: a critical ratio/amount calculation uses a stale accumulated value (cached price instead of accrued interest) without first refreshing it.

### Finding Description
`LRTOracle.rsETHPrice` is a storage variable updated only when `updateRSETHPrice()` is explicitly called. Between updates, the actual value of the underlying restaked assets grows continuously (EigenLayer staking rewards, LST appreciation). Neither `initiateWithdrawal` nor `instantWithdrawal` calls `updateRSETHPrice()` before computing the asset amount owed to the user.

In `initiateWithdrawal`:
```
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);  // line 168
```

In `instantWithdrawal`:
```
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);  // line 228
```

`getExpectedAssetAmount` computes:
```
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);  // line 593
```

`lrtOracle.rsETHPrice()` is the last stored value from the most recent `updateRSETHPrice()` call, which may be arbitrarily old. `updateRSETHPrice()` is a separate public function with no automatic invocation inside the withdrawal path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

When `rsETHPrice` is stale (lower than the true current rate because rewards have accrued since the last oracle update), users who call `initiateWithdrawal` or `instantWithdrawal` receive fewer underlying assets than the fair current value of their rsETH. The stored `expectedAssetAmount` is locked in at initiation time using the stale rate and is what the user ultimately receives upon `completeWithdrawal`. The protocol does not lose funds in this direction, but users are systematically shortchanged relative to the true rsETH/ETH exchange rate at the time of their withdrawal request.

The converse direction (stale price higher than actual) would cause the protocol to over-pay users, but this is less likely given that rsETH is a yield-bearing token whose price trends upward. [6](#0-5) 

### Likelihood Explanation
**Medium.** `updateRSETHPrice()` is a public function that must be called externally; it is not invoked atomically inside the withdrawal path. The protocol relies on off-chain keepers or manual calls to keep `rsETHPrice` fresh. Any gap between the last oracle update and a user's withdrawal call results in the stale-price condition. Given that staking rewards accrue continuously and oracle updates are periodic (not per-block), this condition is the norm rather than the exception. Any unprivileged user can trigger `initiateWithdrawal` or `instantWithdrawal` at any time. [4](#0-3) 

### Recommendation
Call `updateRSETHPrice()` (or its internal equivalent `_updateRsETHPrice()`) at the beginning of `initiateWithdrawal` and `instantWithdrawal`, before `getExpectedAssetAmount` is invoked. This ensures the exchange rate used to compute the user's asset entitlement reflects all accrued yield up to the current block, directly mirroring the fix described in the external report (calling `accrue_vault_interest` before computing the collateral ratio).

```solidity
function initiateWithdrawal(address asset, uint256 rsETHUnstaked, string calldata referralId) external ... {
    // Refresh oracle price before computing withdrawal amount
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    ...
    uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
    ...
}
```

Apply the same fix to `instantWithdrawal`. [7](#0-6) [8](#0-7) 

### Proof of Concept
1. At block N, `updateRSETHPrice()` is called. `rsETHPrice` is stored as `1.05e18` (1.05 ETH per rsETH).
2. Staking rewards accrue over the next 24 hours. The true rsETH/ETH rate rises to `1.06e18`, but `updateRSETHPrice()` has not been called again.
3. A user holds 100 rsETH and calls `initiateWithdrawal(stETH, 100e18, "")`.
4. `getExpectedAssetAmount` computes: `100e18 * 1.05e18 / stETHPrice`. The user's `expectedAssetAmount` is locked in using the stale `1.05e18` rate.
5. Had `updateRSETHPrice()` been called first, the rate would be `1.06e18` and the user would receive ~0.95% more stETH.
6. The user completes the withdrawal and receives the under-valued amount. The difference (accrued yield) is effectively trapped in the protocol rather than delivered to the withdrawing user. [3](#0-2) [9](#0-8)

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

**File:** contracts/LRTOracle.sol (L214-250)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```
