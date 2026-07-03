Audit Report

## Title
rsETH Share Price Inflation via ETH Donation Enables Zero-Mint Deposit Theft - (File: contracts/LRTDepositPool.sol)

## Summary
An attacker can inflate `rsETHPrice` in `LRTOracle` by donating ETH directly to `LRTDepositPool` via its open `receive()` and calling the permissionless `updateRSETHPrice()`. Because `pricePercentageLimit` defaults to `0` and `_beforeDeposit` has no zero-mint guard, a subsequent depositor who passes `minRSETHAmountExpected = 0` receives 0 rsETH while their full ETH deposit is retained by the pool, effectively transferring it to the attacker's single-wei rsETH position.

## Finding Description

**Root cause chain:**

1. `LRTDepositPool.receive()` accepts ETH from any caller with no restriction. [1](#0-0) 

2. `getETHDistributionData()` uses `address(this).balance` as the ETH lying in the pool, so donated ETH is immediately counted as protocol TVL. [2](#0-1) 

3. `LRTOracle._getTotalEthInProtocol()` calls `getTotalAssetDeposits(ETH)` → `getETHDistributionData()`, so the donated ETH inflates `totalETHInProtocol`. [3](#0-2) 

4. `updateRSETHPrice()` is public and permissionless. [4](#0-3) 

5. The new price is computed as `(totalETHInProtocol - fee).divWad(rsethSupply)`. With `rsethSupply = 1 wei` and `totalETHInProtocol ≈ 2e18`, `divWad` (which multiplies by 1e18 before dividing) yields `newRsETHPrice ≈ 2e36`. [5](#0-4) 

6. The price-increase guard is gated on `pricePercentageLimit > 0`. Since `initialize()` never sets `pricePercentageLimit`, it defaults to `0`, making the guard permanently inactive on a fresh deployment. [6](#0-5) [7](#0-6) 

7. `getRsETHAmountToMint` divides by the now-inflated `rsETHPrice`: `(1e18 * 1e18) / 2e36 = 0` (integer truncation). [8](#0-7) 

8. `_beforeDeposit` only checks `rsethAmountToMint < minRSETHAmountExpected`. When both are `0`, the check `0 < 0` is false and execution continues. [9](#0-8) 

9. `_mintRsETH(0)` has no zero-amount guard; it calls `IRSETH.mint(msg.sender, 0)`, which succeeds under standard ERC20 semantics. The victim's ETH (already transferred via `msg.value`) remains in the pool. [10](#0-9) 

## Impact Explanation

**Critical — direct theft of user funds.** The victim's entire ETH deposit is accepted by the contract and 0 rsETH is minted in return. The attacker's single wei of rsETH represents a proportional claim on the entire pool TVL (including the victim's deposit). Upon withdrawal, the attacker recovers their donated ETH plus the victim's deposit. This matches the allowed impact class: *Direct theft of any user funds, whether at-rest or in-motion*.

## Likelihood Explanation

Two conditions must hold simultaneously, both of which are default/common states:

- `pricePercentageLimit == 0`: this is the **default** because `initialize()` never sets it. Every fresh deployment is vulnerable until an admin explicitly calls `setPricePercentageLimit`.
- `minRSETHAmountExpected == 0`: common when users interact directly with the contract, use scripts without slippage protection, or when frontends omit the parameter.

The attack requires no special privileges, only ETH capital comparable to the victim's deposit, and is executable as a straightforward front-run.

## Recommendation

1. **Enforce a non-zero minimum rsETH output**: in `_beforeDeposit`, add `if (rsethAmountToMint == 0) revert ZeroRsETHMinted();` before the slippage check.
2. **Set `pricePercentageLimit` in `initialize()`** to a safe non-zero value (e.g., `1e16` for 1%) so unprivileged callers cannot inflate the price arbitrarily in a single transaction.
3. **Add virtual balances** to the TVL calculation (analogous to OpenZeppelin ERC4626's `_decimalsOffset`) so a minimal rsETH supply cannot produce extreme per-share prices.

## Proof of Concept

```
Preconditions: pricePercentageLimit == 0 (default), minAmountToDeposit == 0 (default)

1. attacker.depositETH{value: 1}(0, "")
   → rsETH supply = 1 wei, address(this).balance = 1 wei, rsETHPrice = 1e18

2. attacker sends 2e18 ETH to LRTDepositPool via receive()
   → address(this).balance = 2e18 + 1

3. attacker calls lrtOracle.updateRSETHPrice()
   → totalETHInProtocol = 2e18 + 1
   → newRsETHPrice = (2e18 + 1) * 1e18 / 1 ≈ 2e36   [divWad multiplies by 1e18]
   → pricePercentageLimit == 0 → isPriceIncreaseOffLimit = false → no revert
   → rsETHPrice stored = 2e36

4. victim.depositETH{value: 1e18}(0, "")
   → rsethAmountToMint = (1e18 * 1e18) / 2e36 = 0
   → _beforeDeposit: 0 < 0 == false → no revert
   → _mintRsETH(0) → victim receives 0 rsETH, loses 1e18 ETH

5. attacker's 1 wei rsETH backs TVL ≈ 3e18 ETH
   → attacker redeems, recovering victim's 1e18 ETH
```

Foundry test plan: deploy `LRTDepositPool` + `LRTOracle` with default `pricePercentageLimit = 0`; execute steps 1–5 above; assert `rsETH.balanceOf(victim) == 0` and `address(lrtDepositPool).balance >= 3e18`.

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L331-343)
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
```
