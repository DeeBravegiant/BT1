### Title
Inflation Attack via Direct Token Donation to `LRTDepositPool` Inflates `rsETHPrice`, Causing Subsequent Depositors to Receive Zero rsETH — (`contracts/LRTDepositPool.sol` / `contracts/LRTOracle.sol`)

---

### Summary

`LRTDepositPool.getAssetDistributionData()` uses `IERC20(asset).balanceOf(address(this))` and `getETHDistributionData()` uses `address(this).balance` to measure protocol TVL. These raw balance reads are consumed by the public, permissionless `LRTOracle.updateRSETHPrice()` to set the stored `rsETHPrice`. An attacker can donate tokens directly to `LRTDepositPool`, call `updateRSETHPrice()` to commit the inflated price, and cause subsequent depositors to receive zero rsETH due to integer rounding — while the attacker redeems their pre-inflation rsETH at the inflated rate, stealing the victim's deposit.

---

### Finding Description

**Root cause 1 — raw `balanceOf` in TVL accounting:**

`getAssetDistributionData()` reads the deposit pool's LST balance directly from the token contract: [1](#0-0) 

`getETHDistributionData()` reads the deposit pool's ETH balance directly: [2](#0-1) 

Both values are summed by `_getTotalEthInProtocol()` in the oracle: [3](#0-2) 

**Root cause 2 — permissionless `updateRSETHPrice()`:**

The function that commits the new price to storage has no access control: [4](#0-3) 

**Root cause 3 — price used directly for minting:**

The rsETH minted per deposit is `(amount × assetPrice) / rsETHPrice`: [5](#0-4) 

If `rsETHPrice` is inflated, the numerator can be less than the denominator, rounding to zero in Solidity.

**Root cause 4 — `pricePercentageLimit` defaults to zero:**

The guard against large price jumps is disabled when `pricePercentageLimit == 0`, which is the default (it is never set in `initialize()`): [6](#0-5) 

The `LRTDepositPool` also accepts arbitrary ETH via an open `receive()`: [7](#0-6) 

---

### Impact Explanation

**Critical — direct theft of user funds.**

Alice deposits 1 wei of stETH and receives 1 rsETH (at the initial price of 1e18). She then transfers `a` stETH directly to `LRTDepositPool` and calls `updateRSETHPrice()`. The new stored price becomes `(1 + a) × 1e18`. Bob deposits `b` stETH; he receives `b × 1e18 / ((1 + a) × 1e18) = b / (1 + a)` rsETH. When `a ≥ b`, this rounds to zero — Bob's stETH is transferred in but no rsETH is minted. Alice calls `updateRSETHPrice()` again; the price is now `(1 + a + b) × 1e18`. Alice redeems her 1 rsETH and recovers `1 + a + b` stETH — her original 1 stETH, her donation of `a` stETH, and Bob's `b` stETH. Bob has permanently lost his deposit.

---

### Likelihood Explanation

**Medium.** The attack requires `pricePercentageLimit == 0` (the default, never set in `initialize()`), a victim who sets `minRSETHAmountExpected = 0` or uses a frontend that does not enforce slippage, and the attacker to front-run the victim's deposit. All three conditions are realistic at protocol launch or when the limit is not yet configured. The attacker's cost equals the donation amount `a`, which must be at least as large as the victim's deposit `b`, making the attack capital-intensive but not impractical for large deposits.

---

### Recommendation

1. **Replace raw `balanceOf` with internal accounting.** Maintain a `mapping(address => uint256) internal depositedAssets` that is incremented only inside `depositAsset()` / `depositETH()` and decremented on withdrawals. Use this mapping instead of `IERC20(asset).balanceOf(address(this))` and `address(this).balance` in `getAssetDistributionData()` and `getETHDistributionData()`.

2. **Add a sweep function** to recover any tokens sent directly to the contract that are not tracked by internal accounting, so they do not silently inflate the price.

3. **Ensure `pricePercentageLimit` is set to a non-zero value during `initialize()`** so that a single large donation cannot commit an arbitrarily inflated price in one transaction.

4. **Enforce `rsethAmountToMint > 0`** inside `_beforeDeposit()` to prevent a depositor from silently losing their entire deposit to rounding.

---

### Proof of Concept

```
State: rsETHPrice = 1e18, rsETH.totalSupply() = 0, pricePercentageLimit = 0

1. Alice calls depositAsset(stETH, 1 wei, 0)
   → rsethAmountToMint = (1 × 1e18) / 1e18 = 1
   → Alice holds 1 rsETH; pool holds 1 wei stETH

2. Alice calls stETH.transfer(LRTDepositPool, a)   // direct donation, no rsETH minted
   → pool.balanceOf(stETH) = 1 + a

3. Alice calls LRTOracle.updateRSETHPrice()
   → totalETHInProtocol ≈ (1 + a) × 1e18
   → newRsETHPrice = (1 + a) × 1e18 / 1 = (1 + a) × 1e18
   → pricePercentageLimit == 0 → no revert
   → rsETHPrice = (1 + a) × 1e18

4. Bob calls depositAsset(stETH, b, 0)   // victim, minRSETHAmountExpected = 0
   → rsethAmountToMint = (b × 1e18) / ((1 + a) × 1e18) = b / (1 + a)
   → if a >= b: rounds to 0 → Bob gets 0 rsETH, but b stETH is transferred in

5. Alice calls LRTOracle.updateRSETHPrice()
   → totalETHInProtocol ≈ (1 + a + b) × 1e18
   → rsETHPrice = (1 + a + b) × 1e18

6. Alice redeems 1 rsETH via withdrawal manager
   → returnAmount = 1 × (1 + a + b) × 1e18 / 1e18 = 1 + a + b stETH

Alice net gain: b stETH (Bob's entire deposit)
Bob net loss:  b stETH (permanent)
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-266)
```text
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

**File:** contracts/LRTOracle.sol (L331-348)
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
```
