### Title
Protocol Assumes 18-Decimal Precision for All Supported Assets in rsETH Minting and TVL Calculations — (`contracts/LRTDepositPool.sol`, `contracts/LRTOracle.sol`, `contracts/LRTConverter.sol`)

---

### Summary

Multiple core calculations in the LRT-rsETH protocol hardcode `1e18` as the decimal normalizer when computing ETH value from asset amounts, implicitly assuming every supported collateral token has exactly 18 decimals. If any non-18-decimal ERC-20 is added as a supported asset, rsETH minting amounts, the protocol-wide TVL used to price rsETH, and the converter's ETH-value accounting will all be computed incorrectly, leading to share mis-accounting and potential protocol insolvency or direct fund theft.

---

### Finding Description

**Root cause 1 — `LRTDepositPool.getRsETHAmountToMint()`** [1](#0-0) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`amount` is expressed in the asset's native decimals. `getAssetPrice(asset)` returns a price normalised to `1e18` (ETH per one whole token). `rsETHPrice()` is also `1e18`-normalised. The formula is only dimensionally correct when `amount` is in `1e18` units. For a token with `d` decimals the result is off by a factor of `10^(18-d)`.

**Root cause 2 — `LRTOracle._getTotalEthInProtocol()`** [2](#0-1) 

```solidity
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

`mulWad` divides by `WAD = 1e18`. [3](#0-2) 

The comment on line 340 explicitly states the assumption: *"totalAssetAmt is in 1e18 precision (standard token decimals)"*. For a 6-decimal token the ETH contribution is understated by `1e12`; for a 24-decimal token it is overstated by `1e6`.

**Root cause 3 — `LRTConverter.transferAssetFromDepositPool()`** [4](#0-3) 

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

Same hardcoded `1e18` divisor; `ethValueInWithdrawal` (used in TVL accounting) is wrong for any non-18-decimal asset.

**Root cause 4 — swap helpers** [5](#0-4) [6](#0-5) 

Both `getSwapETHToAssetReturnAmount` and `getSwapAssetForETHReturnAmount` divide or multiply by `1e18` without normalising for the asset's actual decimals.

---

### Impact Explanation

Consider a 6-decimal token (e.g., USDC) added as a supported asset with a price of `1e18` (1:1 with ETH):

| Scenario | Expected | Actual | Error |
|---|---|---|---|
| User deposits 1 token (1e6 units) → rsETH minted | `1e18` rsETH | `1e6` rsETH | `1e12×` too little |
| TVL contribution of 1 token | `1e18` wei-ETH | `1e6` wei-ETH | `1e12×` too small |

A massively understated TVL causes `rsETHPrice` to be understated, so depositors of 18-decimal tokens subsequently receive `1e12×` more rsETH than they should — a direct theft of value from existing rsETH holders. Conversely, for a token with more than 18 decimals, TVL is overstated, rsETH price is inflated, and depositors of that token receive far more rsETH than the value they contributed, draining the protocol.

Impact classification: **Critical — protocol insolvency / direct theft of user funds**.

---

### Likelihood Explanation

The `LRTConfig` contract's `addNewSupportedAsset` function is callable by the admin and places no restriction on the token's decimal count. [7](#0-6) 

The protocol documentation states it accepts *"any non-rebasing ERC20"* as collateral. Common LSTs and yield tokens (e.g., WBTC at 8 decimals, or future tokens) are non-18-decimal. The moment any such token is added, every depositor and rsETH holder is affected. Likelihood: **Medium** (requires admin to add a non-18-decimal asset, which is an explicitly supported operation).

---

### Recommendation

Normalise `amount` (and `totalAssetAmt`) to 18 decimals before performing WAD arithmetic. For example, in `getRsETHAmountToMint`:

```solidity
uint8 assetDecimals = IERC20Metadata(asset).decimals();
uint256 normalizedAmount = amount * 10 ** (18 - assetDecimals);
rsethAmountToMint = (normalizedAmount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Apply the same normalisation in `_getTotalEthInProtocol`, `transferAssetFromDepositPool`, and both swap helpers.

---

### Proof of Concept

1. Admin calls `LRTConfig.addNewSupportedAsset(USDC, depositLimit)` — USDC has 6 decimals.
2. Admin sets a Chainlink price oracle for USDC returning `1e18` (1 USDC ≈ 1 ETH for simplicity).
3. Attacker calls `LRTDepositPool.depositAsset(USDC, 1_000_000e6, 0, "")` — depositing 1,000,000 USDC.
4. `getRsETHAmountToMint` computes: `(1_000_000e6 * 1e18) / rsETHPrice`. With `rsETHPrice = 1e18`, result = `1_000_000e6` = `1e12` rsETH instead of `1e30` rsETH.
5. Simultaneously, `_getTotalEthInProtocol` adds `1_000_000e6 * 1e18 / 1e18 = 1_000_000e6` wei to TVL instead of `1_000_000e18` wei, causing rsETH price to collapse.
6. A second user depositing 1 stETH (1e18 units) now receives `(1e18 * 1e18) / rsETHPrice_collapsed` rsETH — massively more than 1 rsETH, draining value from all prior holders.

### Citations

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L539-541)
```text
        uint256 ethPricePerUint = 1e18;

        return ethPricePerUint * ethAmountToSend / lrtOracle.getAssetPrice(toAsset);
```

**File:** contracts/LRTDepositPool.sol (L559-561)
```text

        return lrtOracle.getAssetPrice(fromAsset) * fromAssetAmount / 1e18;
    }
```

**File:** contracts/LRTOracle.sol (L338-344)
```text
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```

**File:** contracts/utils/WadMath.sol (L17-19)
```text
    function mulWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x.mulDiv(y, WAD);
    }
```

**File:** contracts/LRTConverter.sol (L140-142)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
```

**File:** contracts/LRTConfig.sol (L49-62)
```text
    function initialize(address admin, address stETH, address ethX, address rsETH_) external initializer {
        UtilLib.checkNonZeroAddress(admin);
        UtilLib.checkNonZeroAddress(rsETH_);

        __AccessControl_init();
        _setToken(LRTConstants.ST_ETH_TOKEN, stETH);
        _setToken(LRTConstants.ETHX_TOKEN, ethX);
        _addNewSupportedAsset(stETH, 100_000 ether);
        _addNewSupportedAsset(ethX, 100_000 ether);

        _grantRole(DEFAULT_ADMIN_ROLE, admin);

        rsETH = rsETH_;
    }
```
