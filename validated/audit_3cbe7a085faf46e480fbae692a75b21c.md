### Title
Missing Decimal Normalization in `getRsETHAmountToMint` and `_getTotalEthInProtocol` Causes Severe Undervaluation for Non-18-Decimal Assets - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint` and `LRTOracle._getTotalEthInProtocol` both treat the raw token amount as if it were already in 18-decimal precision. When a supported asset has fewer than 18 decimals, the rsETH minted to a depositor is scaled down by `10^(18 - assetDecimals)`, causing a near-total loss of deposited value, and the protocol TVL is similarly undercounted, corrupting the rsETH price for all holders.

---

### Finding Description

`getRsETHAmountToMint` computes the rsETH to mint as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`amount` is the raw ERC-20 balance in the asset's native decimals. `getAssetPrice` returns a value in 1e18 precision, and `rsETHPrice` is also 1e18. For an 18-decimal asset the formula is correct. For a 6-decimal asset (e.g. USDC), the numerator is `amount_6 * price_18`, which after dividing by `rsETHPrice_18` yields a result in 6-decimal precision — **1 000 000 000 000× smaller than the correct 18-decimal rsETH amount**.

The same raw amount flows into `_getTotalEthInProtocol`:

```solidity
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

`mulWad` is `a * b / 1e18`. With `totalAssetAmt` in 6-decimal native units and `assetER` in 18-decimal precision, the product is divided by 1e18, leaving the contribution in 6-decimal precision — **undercounting the ETH value by a factor of 1e12** for a 6-decimal asset. This corrupts `rsETHPrice`, which is then used to mint rsETH for every subsequent depositor.

The same decimal-blind multiplication appears in `RSETHPoolV3.viewSwapRsETHAmountAndFee` for token deposits:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

Again, `amountAfterFee` is in native token decimals, while both rates are 18-decimal, so the result is in native-decimal precision rather than 18-decimal rsETH units.

---

### Impact Explanation

A user depositing 1 000 USDC (6 decimals, `amount = 1_000e6`) at a USDC/ETH rate of ~0.00055 and rsETH price of ~1.05 ETH would receive:

```
rsethAmountToMint = (1_000e6 * 0.00055e18) / 1.05e18
                 ≈ 5.24e5   (≈ 0.000000524 rsETH)
```

The correct amount is `≈ 0.524e18` (0.524 rsETH). The depositor loses **~99.9999% of their deposit value** — a direct, permanent theft of user funds. Simultaneously, the TVL undercount in `_getTotalEthInProtocol` deflates `rsETHPrice`, diluting every existing rsETH holder.

---

### Likelihood Explanation

The vulnerability is latent: it activates the moment a governance-controlled admin adds any ERC-20 asset with fewer than 18 decimals to the supported asset list (via `LRTConfig` for `LRTDepositPool`, or via `addSupportedToken` for `RSETHPoolV3`). No code path restricts asset decimals to 18. The protocol is explicitly designed to be extensible to new collateral types, and common liquid-staking or collateral tokens with non-18 decimals (USDC, USDT, WBTC) are plausible additions. The admin need not be malicious — the bug fires silently on any legitimate addition of such an asset.

---

### Recommendation

Normalize `amount` to 18-decimal precision before performing price arithmetic. Introduce a helper that reads `IERC20Metadata(asset).decimals()` and scales accordingly:

```solidity
uint256 normalizedAmount = amount * 10 ** (18 - IERC20Metadata(asset).decimals());
rsethAmountToMint = (normalizedAmount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Apply the same normalization in `_getTotalEthInProtocol` before calling `mulWad`, and in every pool's `viewSwapRsETHAmountAndFee` before multiplying by the token-to-ETH rate. Alternatively, enforce at asset-registration time that only 18-decimal tokens may be added.

---

### Proof of Concept

**Root cause — `getRsETHAmountToMint` (no decimal normalization):** [1](#0-0) 

**Root cause — `_getTotalEthInProtocol` (raw balance fed into `mulWad`):** [2](#0-1) 

**Root cause — `RSETHPoolV3.viewSwapRsETHAmountAndFee` for tokens (no decimal normalization):** [3](#0-2) 

**Entry path — any user calling `depositAsset` with a supported non-18-decimal token:** [4](#0-3) 

**Admin gate — `addSupportedToken` has no decimal restriction:** [5](#0-4)

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L340-343)
```text
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/pools/RSETHPoolV3.sol (L324-334)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-554)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
```
