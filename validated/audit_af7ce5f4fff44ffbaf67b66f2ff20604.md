### Title
Stale `rsETHPrice` in `getRsETHAmountToMint()` Allows Depositors to Receive Excess rsETH, Diluting Existing Holders' Yield - (File: contracts/LRTDepositPool.sol)

---

### Summary

The `getRsETHAmountToMint()` function in `LRTDepositPool` calculates rsETH to mint using a **stale cached `rsETHPrice`** from `LRTOracle`. This price is only updated when `updateRSETHPrice()` is explicitly called. As staking rewards accrue between updates, the actual rsETH price rises above the stored value. Any depositor who deposits during this window receives more rsETH than their contribution is worth, effectively stealing accrued yield from existing rsETH holders.

---

### Finding Description

The minting formula in `getRsETHAmountToMint()` is:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` reads a **live** price from Chainlink or a protocol-specific oracle. However, `lrtOracle.rsETHPrice()` returns the **stored state variable** `rsETHPrice` in `LRTOracle`, which is only updated when `_updateRsETHPrice()` is triggered via `updateRSETHPrice()` (public) or `updateRSETHPriceAsManager()` (manager-only). [1](#0-0) 

The stored `rsETHPrice` is computed as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [2](#0-1) 

Between calls to `updateRSETHPrice()`, ETH staking rewards accrue and LST prices appreciate, causing the true rsETH price (TVL / supply) to rise above the stored `rsETHPrice`. The deposit functions `depositETH()` and `depositAsset()` never call `updateRSETHPrice()` before computing the mint amount: [3](#0-2) [4](#0-3) 

A depositor who deposits while `rsETHPrice` is stale (lower than actual) receives:

```
rsethMinted = deposit * assetPrice / rsETHPrice_stale
```

which is **greater** than the correct amount:

```
rsethCorrect = deposit * assetPrice / rsETHPrice_actual
```

The excess rsETH represents a transfer of value from existing holders to the new depositor.

Additionally, `updateRSETHPrice()` can revert for non-manager callers when the price increase exceeds `pricePercentageLimit`, creating extended windows of enforced staleness: [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When a depositor mints rsETH against a stale (lower) `rsETHPrice`, they receive excess rsETH proportional to the yield that accrued since the last price update. When `updateRSETHPrice()` is eventually called, the new price is computed as `totalETHInProtocol / rsethSupply`. Because `rsethSupply` is inflated by the excess minted tokens, the price settles lower than it should, permanently diluting every existing holder's share of the TVL. The stolen yield is the difference between the correct post-update price and the actual post-update price, multiplied by the existing supply.

---

### Likelihood Explanation

**Medium.** ETH staking rewards accrue every block, so `rsETHPrice` is perpetually slightly stale between updates. A depositor does not need to perform any special setup — they simply deposit when the price has not been recently updated. The depositor has a direct financial incentive to avoid calling `updateRSETHPrice()` before depositing (doing so would reduce their rsETH output). The window of exploitability is persistent and continuous.

---

### Recommendation

Call `_updateRsETHPrice()` (or an equivalent on-the-fly computation) atomically within `_beforeDeposit()` before computing `getRsETHAmountToMint()`, so that the rsETH price always reflects the current TVL and supply at the moment of deposit. Alternatively, compute the rsETH price on-the-fly from live TVL and supply rather than reading the cached state variable.

---

### Proof of Concept

**Setup:**
- Protocol TVL: 100 ETH, rsETH supply: 100, `rsETHPrice` = 1e18 (1 ETH per rsETH)
- 1 ETH of staking rewards accrue → true price should be 101/100 = 1.01e18
- `updateRSETHPrice()` is NOT called

**Attack:**
1. Attacker calls `depositETH{ value: 1 ether }(0, "")`.
2. `getRsETHAmountToMint(ETH, 1e18)` computes: `1e18 * 1e18 / 1e18 = 1e18` → mints **1 rsETH**.
3. Correct amount at true price: `1e18 * 1e18 / 1.01e18 ≈ 0.9901e18` rsETH.
4. Attacker received **~0.0099 rsETH excess**.

**After `updateRSETHPrice()` is called:**
- TVL = 102 ETH, supply = 101 rsETH → price = 102/101 ≈ **1.0099e18**
- Correct price (if attacker got 0.9901 rsETH): 102/100.9901 ≈ **1.0100e18**
- Existing holders' rsETH is worth ~0.01% less than it should be — the attacker extracted that yield.

This exploit is repeatable every time rewards accrue before a price update, and scales with deposit size and the duration of price staleness. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-222)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
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
