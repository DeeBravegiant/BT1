### Title
Stale Cached `rsETHPrice` Used for Deposit Minting and Instant Withdrawal Valuation Enables Share Price Manipulation - (File: `contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTOracle.rsETHPrice` is a cached state variable updated only when `updateRSETHPrice()` is explicitly called. Both `LRTDepositPool.getRsETHAmountToMint()` and `LRTWithdrawalManager.getExpectedAssetAmount()` divide by this stale value while simultaneously reading **live** asset prices via `getAssetPrice()`. The resulting price mismatch is directly analogous to the reported vault using a spot oracle price instead of the true mark price: an attacker can time deposits or instant-withdrawals around the staleness window to receive more rsETH (or more underlying assets) than their fair share, stealing value from other depositors.

---

### Finding Description

`LRTOracle` stores `rsETHPrice` as a persistent state variable:

```solidity
uint256 public override rsETHPrice;
```

It is only updated when `_updateRsETHPrice()` is called, either via the public `updateRSETHPrice()` or the manager-gated `updateRSETHPriceAsManager()`. There is no on-chain requirement to refresh this value before any user-facing operation.

**Deposit path** (`LRTDepositPool.getRsETHAmountToMint`):

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`getAssetPrice(asset)` fetches a **live** Chainlink price on every call, while `rsETHPrice()` returns the **stale cached** value. If underlying LST prices have risen since the last `updateRSETHPrice()` call, the true rsETHPrice is higher than the stored value. A depositor who acts before the update receives more rsETH than their deposit is worth, diluting all existing holders.

**Instant-withdrawal path** (`LRTWithdrawalManager.instantWithdrawal` → `getExpectedAssetAmount`):

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

Again, `rsETHPrice()` is stale while `getAssetPrice(asset)` is live. If underlying LST prices have fallen since the last update, the true rsETHPrice is lower than the stored value. An attacker who calls `instantWithdrawal` before `updateRSETHPrice()` is called receives more underlying assets than their rsETH is truly worth. Unlike the queued-withdrawal path, `instantWithdrawal` has **no** `min(expectedAmount, currentReturn)` guard — it transfers the full stale-inflated amount immediately.

The `_updateRsETHPrice` function itself computes the true price from live asset prices:

```solidity
uint256 totalETHInProtocol = _getTotalEthInProtocol(); // uses live getAssetPrice()
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

But this computation is never triggered atomically by deposits or withdrawals. The gap between the live true price and the stored stale price is the exploitable basis.

---

### Impact Explanation

**Critical — direct theft of user funds.**

- **Instant-withdrawal scenario:** When LST prices drop and `rsETHPrice` is stale-high, an attacker burns rsETH and receives more underlying LST than the rsETH is worth. The excess comes directly from the pool's assets, reducing the redemption value for all remaining depositors.
- **Deposit scenario:** When LST prices rise and `rsETHPrice` is stale-low, an attacker mints more rsETH than their deposit warrants. After `updateRSETHPrice()` is called, the attacker's rsETH is worth more than deposited, and the dilution is borne by all existing holders.

Both paths result in direct, quantifiable loss to other protocol participants.

---

### Likelihood Explanation

**Medium.**

- `updateRSETHPrice()` is public and called by off-chain bots, but there is no on-chain freshness enforcement. Any block between two update calls is a valid attack window.
- LST prices (stETH/ETH, ETHx/ETH) fluctuate continuously via Chainlink. Even small deviations (0.1–0.5%) over a multi-block window are sufficient for a profitable attack at scale.
- `instantWithdrawal` must be enabled by the manager (`isInstantWithdrawalEnabled[asset]`), which gates the most direct path. However, the deposit-side dilution path is always open when the protocol is unpaused.
- The attacker needs no special role — any rsETH holder or ETH depositor can execute this.

---

### Recommendation

1. **Atomically refresh `rsETHPrice` before every deposit and withdrawal.** Call `_updateRsETHPrice()` (or an equivalent view-only live computation) inside `_beforeDeposit` and `getExpectedAssetAmount` rather than reading the cached state variable.
2. **Alternatively, compute the share price on-the-fly** using `_getTotalEthInProtocol() / rsethSupply` at the point of use, eliminating the cached value entirely for user-facing operations.
3. **Add a staleness guard** on `rsETHPrice` (e.g., a `lastUpdatedTimestamp` that must be within N blocks) and revert deposits/withdrawals if the price is stale.

---

### Proof of Concept

**Instant-withdrawal theft (when `isInstantWithdrawalEnabled[stETH] == true`):**

1. At block B: stETH/ETH Chainlink price = 1.01. `updateRSETHPrice()` is called; `rsETHPrice` = 1.01. Protocol holds 10,000 stETH, 9,901 rsETH outstanding.
2. At block B+10: stETH/ETH Chainlink price drops to 0.99 (e.g., depeg event). True rsETHPrice = `10,000 * 0.99 / 9,901` ≈ 0.99. Stored `rsETHPrice` = 1.01 (stale — `updateRSETHPrice()` not yet called).
3. Attacker (holds 1,000 rsETH) calls `instantWithdrawal(stETH, 1000)`:
   - `getExpectedAssetAmount(stETH, 1000)` = `1000 * 1.01 / 0.99` ≈ **1020.2 stETH**
   - Fair value: `1000 * 0.99 / 0.99` = **1000 stETH**
   - Attacker receives **~20.2 stETH excess**, stolen from remaining depositors.
4. `updateRSETHPrice()` is subsequently called; remaining depositors' rsETH is now backed by fewer assets.

**Deposit-side dilution (always available):**

1. At block B: stETH/ETH = 0.99. `rsETHPrice` = 0.99 (updated).
2. At block B+5: stETH/ETH rises to 1.01 (live Chainlink). True rsETHPrice ≈ 1.01. Stored `rsETHPrice` = 0.99 (stale).
3. Attacker deposits 1,000 stETH:
   - `getRsETHAmountToMint(stETH, 1000)` = `1000 * 1.01 / 0.99` ≈ **1020.2 rsETH**
   - Fair amount: `1000 * 1.01 / 1.01` = **1000 rsETH**
   - Attacker receives **~20.2 rsETH excess** at existing holders' expense.
4. `updateRSETHPrice()` is called; attacker's rsETH is now worth more than deposited. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L212-252)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L589-594)
```text
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
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
    }
```
