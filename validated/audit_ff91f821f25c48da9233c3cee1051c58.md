### Title
Incorrect Token Decimal Assumption in `getRsETHAmountToMint()` and `_getTotalEthInProtocol()` Enables Massive rsETH Over-Minting for Non-18-Decimal Assets - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

### Summary
`LRTDepositPool.getRsETHAmountToMint()` and `LRTOracle._getTotalEthInProtocol()` silently assume every supported asset has 18 decimals. If a non-18-decimal token is ever added via `LRTConfig.addNewSupportedAsset()`, the rsETH minting formula and the protocol-wide TVL calculation both produce values that are off by `10^(18 - tokenDecimals)`, enabling a depositor to drain rsETH supply or corrupt the rsETH price for all holders.

---

### Finding Description

**`LRTDepositPool.getRsETHAmountToMint()`** [1](#0-0) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`getAssetPrice(asset)` returns the price of **1e18 raw units** of the asset expressed in 1e18-precision ETH (i.e. it is calibrated for 18-decimal tokens). For a 6-decimal token such as USDC, `amount` arrives as `1e6` (1 USDC), but the formula treats it as if it were `1e6 / 1e18 = 1e-12` of a token, producing an `rsethAmountToMint` that is `1e12` times larger than correct.

**`LRTOracle._getTotalEthInProtocol()`** [2](#0-1) 

```solidity
// totalAssetAmt is in 1e18 precision (standard token decimals)   ← incorrect for non-18-decimal tokens
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

`getTotalAssetDeposits()` returns the raw ERC-20 balance of the asset. [3](#0-2) 

For a 6-decimal token the raw balance is in units of `1e6`, so `mulWad(rawBalance, assetER)` = `rawBalance * assetER / 1e18` understates the true ETH value by `1e12`, collapsing the computed rsETH price and allowing every subsequent depositor to mint rsETH at a fraction of the correct price.

**`LRTConverter.transferAssetFromDepositPool()`** has the same flaw: [4](#0-3) 

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

For a 6-decimal token this inflates `ethValueInWithdrawal` by `1e12`, which feeds back into `getETHDistributionData()` and again corrupts the rsETH price.

**`LRTWithdrawalManager.getExpectedAssetAmount()`** mirrors the same pattern in the withdrawal direction: [5](#0-4) 

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

For a 6-decimal asset this returns a value `1e12` times too large, causing the withdrawal manager to attempt to disburse far more tokens than it holds.

---

### Impact Explanation

**Impact: Critical** — Direct theft of user funds / protocol insolvency.

Scenario (6-decimal token, e.g. USDC at $1, ETH at $2500):
- `getAssetPrice(USDC)` ≈ `4e14` (price of 1e18 raw USDC units in ETH, 1e18-precision)
- `rsETHPrice` ≈ `1.05e18`
- Depositor sends 1 USDC (`amount = 1e6`)
- `rsethAmountToMint = 1e6 * 4e14 / 1.05e18 ≈ 3.8e2` → **~380 rsETH** minted for $1 of USDC

The depositor extracts ~380× the rsETH they are entitled to, draining the protocol's rsETH supply and socialising the loss across all existing rsETH holders through a permanently deflated rsETH price.

---

### Likelihood Explanation

**Likelihood: Low** — All currently deployed supported assets (stETH, ETHx, swETH, etc.) are 18-decimal LSTs, so the bug is latent today. However:

- `LRTConfig.addNewSupportedAsset()` imposes no decimal check. [6](#0-5) 

- The RSETHPool family on L2 exposes `addSupportedToken()` for arbitrary ERC-20s, and the token-deposit path (`deposit(token, amount, referralId)`) has the same decimal-blind formula. [7](#0-6) 

Any future governance decision to accept a non-18-decimal collateral (USDC, WBTC, etc.) immediately activates the vulnerability for any unprivileged depositor.

---

### Recommendation

1. Normalise raw token amounts to 18-decimal precision before any arithmetic that mixes them with 1e18-precision prices:
   ```solidity
   uint256 normalised = amount * 10 ** (18 - IERC20Metadata(asset).decimals());
   rsethAmountToMint = (normalised * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
   ```
2. Apply the same normalisation in `_getTotalEthInProtocol()`, `transferAssetFromDepositPool()`, `getExpectedAssetAmount()`, and every `viewSwapRsETHAmountAndFee(amount, token)` overload across all RSETHPool variants.
3. Add a `require(IERC20Metadata(asset).decimals() == 18, "unsupported decimals")` guard in `addNewSupportedAsset()` / `addSupportedToken()` until full decimal-normalisation is implemented.

---

### Proof of Concept

```
Given:
  USDC decimals = 6
  getAssetPrice(USDC) = 4e14   (≈ 0.0004 ETH per 1e18 raw units, 1e18-precision)
  rsETHPrice = 1.05e18

Attacker calls LRTDepositPool.depositAsset(USDC, 1e6, 0, ""):
  getRsETHAmountToMint(USDC, 1e6)
    = (1e6 * 4e14) / 1.05e18
    = 4e20 / 1.05e18
    ≈ 380e18  →  380 rsETH minted

Correct value:
  1 USDC ≈ 0.0004 ETH → 0.0004 / 1.05 ≈ 3.8e-4 rsETH = 3.8e14 raw rsETH units

Attacker receives 380 rsETH instead of 0.00038 rsETH — a 1,000,000× over-mint.
```

### Citations

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L338-343)
```text
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTConverter.sol (L140-140)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTConfig.sol (L106-117)
```text
    function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
        UtilLib.checkNonZeroAddress(asset);
        if (depositLimit == 0) {
            revert InvalidDepositLimit();
        }
        if (isSupportedAsset[asset]) {
            revert AssetAlreadySupported();
        }
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
        depositLimitByAsset[asset] = depositLimit;
        emit AddedNewSupportedAsset(asset, depositLimit);
```

**File:** contracts/pools/RSETHPool.sol (L326-347)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
