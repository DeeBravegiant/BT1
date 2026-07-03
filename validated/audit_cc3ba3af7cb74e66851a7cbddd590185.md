### Title
First Depositor rsETH Share Inflation via Unrestricted ETH Donation and Public Price Update - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool` is vulnerable to a first-depositor share inflation attack. An attacker can deposit 1 wei of ETH to mint 1 unit of rsETH, then donate a large ETH amount directly to `LRTDepositPool` via its unrestricted `receive()` function, call the public `updateRSETHPrice()` to commit the inflated price, and cause subsequent depositors who pass `minRSETHAmountExpected = 0` to receive 0 rsETH in exchange for their full deposit. The attacker's single rsETH unit then represents 100% of the pool's TVL, including the victim's funds.

---

### Finding Description

**Step 1 — No zero-share guard in `_beforeDeposit`.**

`_beforeDeposit` computes `rsethAmountToMint` and only checks it against the caller-supplied `minRSETHAmountExpected`:

```solidity
// contracts/LRTDepositPool.sol L665-L669
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

There is no `require(rsethAmountToMint != 0)`. If `minRSETHAmountExpected == 0` (the default for many integrations and naive callers), a zero-share mint silently succeeds and `_mintRsETH(0)` is called. [1](#0-0) 

**Step 2 — `getRsETHAmountToMint` uses integer division against the stored `rsETHPrice`.**

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

If `rsETHPrice` has been inflated to a value larger than `amount * assetPrice`, the result truncates to 0. [2](#0-1) 

**Step 3 — `rsETHPrice` is a stored value updated by the public `updateRSETHPrice()`.**

```solidity
// contracts/LRTOracle.sol L87-L89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Any unprivileged caller can trigger a price update at any time. [3](#0-2) 

**Step 4 — `_updateRsETHPrice` computes price as `totalETHInProtocol / rsethSupply`, and `totalETHInProtocol` includes the raw ETH balance of `LRTDepositPool`.**

```solidity
// contracts/LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

`_getTotalEthInProtocol` calls `ILRTDepositPool.getTotalAssetDeposits(ETH_TOKEN)`, which calls `getETHDistributionData()`, which includes `address(this).balance` — i.e., any ETH sitting in the deposit pool contract. [4](#0-3) [5](#0-4) 

**Step 5 — `LRTDepositPool.receive()` is unrestricted; anyone can donate ETH.**

```solidity
// contracts/LRTDepositPool.sol L58
receive() external payable { }
```

This is the donation vector. Sending ETH here immediately inflates `totalETHInProtocol` without minting any rsETH. [6](#0-5) 

**Step 6 — `pricePercentageLimit` defaults to 0, disabling the only price-spike guard.**

```solidity
// contracts/LRTOracle.sol L256-L257
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

`pricePercentageLimit` is never set in `initialize()`, so it is 0. The short-circuit `pricePercentageLimit > 0` makes `isPriceIncreaseOffLimit` permanently `false`, meaning an arbitrarily large price jump passes through `updateRSETHPrice()` in a single call. [7](#0-6) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

A victim who deposits Y wei of ETH with `minRSETHAmountExpected = 0` receives 0 rsETH. Their Y wei is permanently absorbed into the pool's TVL. The attacker, holding the only rsETH unit, now owns 100% of the pool (1 wei deposit + X wei donation + Y wei victim deposit). When the attacker redeems or sells their rsETH, they recover `1 + X + Y` wei, netting a profit of `Y − 1` wei — effectively the victim's entire deposit.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. Being the first depositor (or acting when rsETH supply is negligibly small — plausible at protocol launch or after a large burn event).
2. Donating at least as much ETH as the intended victim's deposit (capital is returned to the attacker via rsETH redemption, so the cost is only the gas and the 1-wei seed deposit).
3. The victim calling `depositETH` with `minRSETHAmountExpected = 0`. This is the default for many contract integrations, aggregators, and naive front-end calls.
4. `pricePercentageLimit == 0` (the uninitialized default).

All four conditions are realistic at launch and require no privileged access.

---

### Recommendation

1. **Add a zero-share guard in `_beforeDeposit`:**
   ```solidity
   require(rsethAmountToMint != 0, "zero rsETH minted");
   ``` [8](#0-7) 

2. **Seed the supply at initialization** (Uniswap V2 pattern): mint a small amount of rsETH to `address(0)` on the first deposit when `totalSupply() == 0`, making the share price manipulation economically impractical.

3. **Set a non-zero `pricePercentageLimit` during `initialize`** so that a single `updateRSETHPrice()` call cannot commit an arbitrarily large price jump. [9](#0-8) 

---

### Proof of Concept

```
Initial state: rsETHPrice = 1e18, rsethSupply = 0

1. Attacker calls depositETH{value: 1}(0, "")
   → rsethAmountToMint = (1 * 1e18) / 1e18 = 1 unit
   → rsethSupply = 1, pool ETH balance = 1 wei

2. Attacker sends X = 10_000 ether directly to LRTDepositPool (receive())
   → pool ETH balance = 1 + 10_000e18 wei
   → rsethSupply still = 1

3. Attacker calls LRTOracle.updateRSETHPrice()
   → totalETHInProtocol = 1 + 10_000e18
   → newRsETHPrice = (1 + 10_000e18) * 1e18 / 1 ≈ 10_000e36
   → rsETHPrice is now ≈ 10_000e36

4. Victim calls depositETH{value: 9_999 ether}(0, "")
   → rsethAmountToMint = (9_999e18 * 1e18) / 10_000e36
                       = 9_999e36 / 10_000e36
                       = 0  (integer truncation)
   → minRSETHAmountExpected = 0, so no revert
   → Victim receives 0 rsETH; 9_999 ETH enters the pool

5. Attacker redeems 1 rsETH unit
   → Represents 100% of pool: 1 + 10_000e18 + 9_999e18 = ~20_000 ETH
   → Attacker net profit ≈ 9_999 ETH (victim's full deposit)
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-481)
```text
        ethLyingInDepositPool = address(this).balance;

```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
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

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```
