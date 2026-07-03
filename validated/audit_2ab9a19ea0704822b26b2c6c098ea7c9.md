### Title
Publicly Callable `updateRSETHPrice()` Enables Sandwich Attack to Steal Yield from rsETH Holders - (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` carries no access restriction and can be called by any address. An attacker can deposit assets at the stale (lower) rsETH price, immediately trigger the price update themselves, then initiate a withdrawal at the newly elevated price — capturing staking yield that should have accrued to existing rsETH holders.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

`_updateRsETHPrice()` computes the new price as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [2](#0-1) 

As EigenLayer staking rewards accrue, `_getTotalEthInProtocol()` grows while `rsethSupply` stays constant, so `newRsETHPrice > rsETHPrice` (the stale stored value). The stored `rsETHPrice` is only updated when `updateRSETHPrice()` is called.

**Deposit minting formula** (in `LRTDepositPool.getRsETHAmountToMint`):

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

A lower `rsETHPrice` means more rsETH minted per unit of asset deposited.

**Withdrawal redemption formula** (in `LRTWithdrawalManager.getExpectedAssetAmount`):

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

A higher `rsETHPrice` means more assets returned per rsETH burned.

**Attack sequence:**

1. Attacker calls `LRTDepositPool.depositETH()` or `depositAsset()` while `rsETHPrice` is stale (lower). They receive an inflated rsETH amount. [5](#0-4) 

2. Attacker calls `LRTOracle.updateRSETHPrice()` directly, pushing `rsETHPrice` up to reflect accrued rewards. [1](#0-0) 

3. Attacker calls `LRTWithdrawalManager.initiateWithdrawal()`. The `expectedAssetAmount` is locked in at the new, higher `rsETHPrice`. [6](#0-5) 

4. After the withdrawal delay (`withdrawalDelayBlocks`, default ~8 days), attacker calls `completeWithdrawal()` and receives more assets than deposited. [7](#0-6) 

**Instant-withdrawal variant:** When `isInstantWithdrawalEnabled[asset]` is `true`, the attacker can collapse steps 1–4 into a single block with no delay, making this a true atomic sandwich:

```
depositETH → updateRSETHPrice → instantWithdrawal
``` [8](#0-7) 

**Partial mitigation — `pricePercentageLimit`:** If `pricePercentageLimit > 0`, a non-manager caller whose update would push the price above the threshold gets a `PriceAboveDailyThreshold` revert. However: (a) if `pricePercentageLimit == 0` there is no cap; (b) the attacker can still profit from any price increase that stays within the limit; (c) the attacker can wait for rewards to accumulate just below the threshold and then strike. [9](#0-8) 

---

### Impact Explanation

The attacker captures staking yield that should have been distributed pro-rata to all existing rsETH holders. They deposit at the stale price (getting more rsETH than fair value), trigger the price update, and redeem at the new price — extracting the reward delta without having held rsETH during the accrual period. This is a direct **theft of unclaimed yield** from legitimate holders. At scale (large deposit, long accrual window), the stolen amount is proportional to the total protocol yield since the last price update.

---

### Likelihood Explanation

- `updateRSETHPrice()` is unconditionally public; no special role or permission is needed.
- The stale-price window is observable on-chain: an attacker can compute `_getTotalEthInProtocol()` off-chain and know exactly how much yield has accrued.
- The attack requires only two standard user-facing transactions (`depositETH`/`depositAsset` and `initiateWithdrawal`) plus one permissionless oracle call.
- The 8-day withdrawal delay is the only friction for the standard path; the instant-withdrawal path removes even that.
- The attack is repeatable every time rewards accrue.

---

### Recommendation

Restrict `updateRSETHPrice()` to authorized callers (e.g., `onlyLRTManager` or a dedicated keeper role), so that an attacker cannot atomically control both the deposit timing and the price-update timing:

```solidity
// Before
function updateRSETHPrice() public whenNotPaused {

// After
function updateRSETHPrice() external whenNotPaused onlyLRTManager {
```

Alternatively, snapshot the rsETH price at deposit time and use the **minimum** of the deposit-time price and the current price when computing the withdrawal amount, preventing any benefit from a price increase that occurs after deposit.

---

### Proof of Concept

```
Assume:
  rsETHPrice (stale) = 1.05e18  (1.05 ETH per rsETH)
  rsETHPrice (new)   = 1.06e18  (after rewards accrued)
  assetPrice (ETH)   = 1e18

Step 1 — depositETH(1 ether):
  rsethMinted = 1e18 * 1e18 / 1.05e18 = 0.9524 rsETH

Step 2 — updateRSETHPrice():
  rsETHPrice updated to 1.06e18

Step 3 — initiateWithdrawal(ETH, 0.9524 rsETH):
  expectedAssetAmount = 0.9524e18 * 1.06e18 / 1e18 = 1.00952 ETH

Step 4 — completeWithdrawal() (after delay):
  Attacker receives 1.00952 ETH, having deposited 1 ETH.
  Profit = 0.00952 ETH per 1 ETH deposited (~0.95% of deposit).

This profit equals the entire yield accrued by the protocol since the last
price update, stolen from legitimate rsETH holders.
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-266)
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

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
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

**File:** contracts/LRTWithdrawalManager.sol (L592-593)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
