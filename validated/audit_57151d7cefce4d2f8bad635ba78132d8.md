Audit Report

## Title
Inflation Attack via Direct Token Donation to `LRTDepositPool` Inflates `rsETHPrice`, Causing Subsequent Depositors to Receive Zero rsETH — (`contracts/LRTDepositPool.sol` / `contracts/LRTOracle.sol`)

## Summary

`LRTDepositPool.getAssetDistributionData()` reads the pool's LST balance via raw `IERC20(asset).balanceOf(address(this))`, and `getETHDistributionData()` reads via `address(this).balance`. These values flow through `getTotalAssetDeposits()` into `LRTOracle._getTotalEthInProtocol()`, which is consumed by the permissionless `updateRSETHPrice()`. An attacker can donate tokens directly to the pool, call `updateRSETHPrice()` to commit an arbitrarily inflated price (bypassed by the default `pricePercentageLimit == 0`), and cause a victim depositor with `minRSETHAmountExpected == 0` to receive zero rsETH while their tokens are permanently absorbed into the pool — recoverable by the attacker upon redemption.

## Finding Description

**Root cause 1 — raw `balanceOf` in TVL accounting:**

`getAssetDistributionData()` reads the deposit pool's LST balance directly from the token contract: [1](#0-0) 

`getETHDistributionData()` reads the deposit pool's ETH balance directly: [2](#0-1) 

Both values are consumed by `_getTotalEthInProtocol()` via `getTotalAssetDeposits()`: [3](#0-2) 

`getTotalAssetDeposits()` delegates entirely to `getAssetDistributionData()`, so any direct token transfer to the pool is immediately reflected in the oracle's TVL: [4](#0-3) 

**Root cause 2 — permissionless `updateRSETHPrice()`:**

The function that commits the new price to storage has no access control: [5](#0-4) 

**Root cause 3 — `pricePercentageLimit` defaults to zero, disabling the price-jump guard:**

The guard is short-circuited when `pricePercentageLimit == 0`, which is the default since `initialize()` never sets it: [6](#0-5) [7](#0-6) 

**Root cause 4 — no `rsethAmountToMint > 0` guard in `_beforeDeposit()`:**

If `minRSETHAmountExpected == 0` and the inflated price causes integer division to round to zero, the deposit proceeds silently with zero rsETH minted: [8](#0-7) 

**Root cause 5 — open `receive()` accepts arbitrary ETH donations:** [9](#0-8) 

**Price used directly for minting:** [10](#0-9) 

## Impact Explanation

**Critical — direct theft of user funds.**

A victim depositor who passes `minRSETHAmountExpected = 0` (the default in many frontends) has their entire deposit transferred into the pool but receives zero rsETH. The attacker, holding pre-inflation rsETH, calls `updateRSETHPrice()` again after the victim's deposit is absorbed, then redeems at the now-higher price, recovering their own capital plus the victim's deposit. This constitutes direct, permanent theft of user funds with no recovery path for the victim.

## Likelihood Explanation

**Medium.** Three conditions must hold simultaneously: (1) `pricePercentageLimit == 0` — the default, never set in `initialize()`, so true at launch and until an admin explicitly configures it; (2) the victim passes `minRSETHAmountExpected = 0` — common in frontends that do not enforce slippage; (3) the attacker front-runs the victim's deposit transaction. All three are realistic at protocol launch. The attacker's cost equals the donation amount `a`, which must be at least as large as the victim's deposit `b`, making the attack capital-intensive but economically rational for large deposits.

## Recommendation

1. **Replace raw `balanceOf` with internal accounting.** Maintain a `mapping(address => uint256) internal depositedAssets` incremented only inside `depositAsset()` / `depositETH()` and decremented on withdrawals. Use this mapping instead of `IERC20(asset).balanceOf(address(this))` and `address(this).balance` in `getAssetDistributionData()` and `getETHDistributionData()`.

2. **Add a sweep function** to recover tokens sent directly to the contract that are not tracked by internal accounting, preventing silent TVL inflation.

3. **Set `pricePercentageLimit` to a non-zero value in `initialize()`** so that a single large donation cannot commit an arbitrarily inflated price in one transaction.

4. **Enforce `rsethAmountToMint > 0` inside `_beforeDeposit()`** to prevent a depositor from silently losing their entire deposit to rounding.

## Proof of Concept

```
Preconditions: rsETHPrice = 1e18, rsETH.totalSupply() = 0,
               pricePercentageLimit = 0, protocolFeeInBPS = 0

1. Alice calls depositAsset(stETH, 1 wei, 0, "")
   → rsethAmountToMint = (1 × 1e18) / 1e18 = 1
   → Alice holds 1 rsETH; pool.balanceOf(stETH) = 1

2. Alice calls stETH.transfer(LRTDepositPool, a)
   → pool.balanceOf(stETH) = 1 + a  (no rsETH minted)

3. Alice calls LRTOracle.updateRSETHPrice()
   → totalETHInProtocol = (1 + a) × 1e18  (via raw balanceOf)
   → newRsETHPrice = (1 + a) × 1e18 / 1 = (1 + a) × 1e18
   → pricePercentageLimit == 0 → isPriceIncreaseOffLimit = false → no revert
   → rsETHPrice = (1 + a) × 1e18

4. Bob calls depositAsset(stETH, b, 0, "")   // minRSETHAmountExpected = 0
   → rsethAmountToMint = (b × 1e18) / ((1 + a) × 1e18) = b / (1 + a)
   → if a >= b: rounds to 0 → no revert (0 < 0 is false)
   → b stETH transferred in, 0 rsETH minted to Bob

5. Alice calls LRTOracle.updateRSETHPrice()
   → totalETHInProtocol = (1 + a + b) × 1e18
   → rsETHPrice = (1 + a + b) × 1e18

6. Alice redeems 1 rsETH via withdrawal manager
   → returnAmount = 1 × (1 + a + b) × 1e18 / 1e18 = 1 + a + b stETH

Alice net gain: b stETH (Bob's entire deposit)
Bob net loss:  b stETH (permanent, no rsETH to redeem)
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
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

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
