The code confirms the claim exactly. Let me verify the key details:

**`_updateRsETHPrice()` in `contracts/LRTOracle.sol`:**
- Line 303: `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` — reverts with `DailyFeeMintLimitExceeded` if limit exceeded [1](#0-0) 
- Line 313: `rsETHPrice = newRsETHPrice` — only executes if line 303 does not revert [2](#0-1) 
- `updateRSETHPrice()` is `public` with only `whenNotPaused` — no access control [3](#0-2) 
- `updateRSETHPriceAsManager()` also calls `_updateRsETHPrice()` and would equally revert [4](#0-3) 

**`getRsETHAmountToMint()` in `contracts/LRTDepositPool.sol`:**
- Line 520 divides by `lrtOracle.rsETHPrice()`, which reads the stale stored value [5](#0-4) 

All code references match the claim. The vulnerability is real and confirmed.

---

Audit Report

## Title
Daily Fee Mint Limit Revert Permanently Blocks `rsETHPrice` Update, Enabling Over-Minting to New Depositors at Existing Holders' Expense - (File: contracts/LRTOracle.sol)

## Summary
In `LRTOracle._updateRsETHPrice()`, the `rsETHPrice = newRsETHPrice` assignment at line 313 is placed after the `_checkAndUpdateDailyFeeMintLimit()` call at line 303. When the computed protocol fee rsETH amount exceeds `maxFeeMintAmountPerDay`, `_checkAndUpdateDailyFeeMintLimit` reverts with `DailyFeeMintLimitExceeded`, rolling back the entire transaction including the price update. The stored `rsETHPrice` remains stale at its previous lower value, causing every subsequent depositor to receive more rsETH than entitled, diluting all existing holders' unclaimed yield.

## Finding Description
`LRTOracle._updateRsETHPrice()` executes the following sequence:

1. Computes `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)` (line 250).
2. Computes `rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice)` (line 301).
3. Calls `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` (line 303), which **reverts** if `currentPeriodMintedFeeAmount + rsethAmountToMintAsProtocolFee > maxFeeMintAmountPerDay`.
4. Mints fee rsETH to treasury (line 306).
5. `rsETHPrice = newRsETHPrice` (line 313).

`_checkAndUpdateDailyFeeMintLimit` unconditionally reverts on limit breach:
```solidity
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
}
```
Because step 3 reverts the entire transaction, step 5 never executes. `updateRSETHPrice()` is `public` with no access control beyond `whenNotPaused`, so any caller can trigger this revert path once the daily limit is hit. `updateRSETHPriceAsManager()` also calls `_updateRsETHPrice()` internally and would equally revert — there is no code path that updates `rsETHPrice` while bypassing `_checkAndUpdateDailyFeeMintLimit`. The price is frozen at the pre-reward value until the manager increases `maxFeeMintAmountPerDay` and manually intervenes.

`LRTDepositPool.getRsETHAmountToMint()` reads the stale `rsETHPrice` directly:
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
A stale (lower) denominator inflates `rsethAmountToMint` for every deposit made while the price is frozen.

## Impact Explanation
**High — Theft of unclaimed yield.**

Every rsETH minted in excess of the correct amount dilutes the ETH-per-rsETH ratio for all existing holders. The accrued rewards that should have been reflected in a higher `rsETHPrice` are instead partially transferred to new depositors who receive excess rsETH at the stale lower price. This is a direct, irreversible transfer of yield from existing holders to late depositors. At protocol scale (hundreds of millions of TVL), even a 1% price gap sustained over a deposit-active window produces material losses for existing holders.

## Likelihood Explanation
**Medium.** No attacker action is required to trigger the revert; it occurs naturally whenever accumulated rewards in a single 24-hour period cause `rsethAmountToMintAsProtocolFee` to exceed `maxFeeMintAmountPerDay`. This is a realistic operational scenario: a large validator reward event, MEV spike, or multi-day price update gap can cause a single call to compute a fee larger than the daily cap. `maxFeeMintAmountPerDay` is calibrated by the manager for normal conditions and may not account for tail events. The attacker only needs to deposit while the price is stale, which requires no special privileges.

## Recommendation
Decouple the price update from the fee-minting guard. Update `rsETHPrice` unconditionally before the fee-minting block, and handle the daily limit by capping the minted fee at the remaining limit or skipping the mint without reverting:

```solidity
// Update price first, unconditionally
rsETHPrice = newRsETHPrice;

if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    uint256 remaining = maxFeeMintAmountPerDay > currentPeriodMintedFeeAmount
        ? maxFeeMintAmountPerDay - currentPeriodMintedFeeAmount
        : 0;
    uint256 mintable = rsethAmountToMintAsProtocolFee > remaining
        ? remaining
        : rsethAmountToMintAsProtocolFee;
    if (mintable > 0) {
        currentPeriodMintedFeeAmount += mintable;
        IRSETH(rsETHTokenAddress).mint(treasury, mintable);
        emit FeeMinted(treasury, mintable);
    }
}

emit RsETHPriceUpdate(rsETHPrice, previousPrice);
```

## Proof of Concept
**Setup:**
- Protocol TVL: 10,000 ETH, rsETH supply: 10,000, `rsETHPrice = 1.000e18`
- `protocolFeeInBPS = 1000` (10%), `maxFeeMintAmountPerDay = 0.5e18` (0.5 rsETH)

**Step 1 — Rewards accrue:**
- Validators earn 10 ETH; `totalETHInProtocol = 10,010 ETH`
- `protocolFeeInETH = 10 × 10% = 1 ETH`
- `newRsETHPrice = (10,010 − 1) / 10,000 = 1.0009e18`
- `rsethAmountToMintAsProtocolFee = 1e18 / 1.0009e18 ≈ 0.9991e18`

**Step 2 — Price update reverts:**
- `_checkAndUpdateDailyFeeMintLimit(0.9991e18)` → `0.9991 > 0.5` → `revert DailyFeeMintLimitExceeded`
- `rsETHPrice` remains `1.000e18` (stale)

**Step 3 — Depositor exploits stale price:**
- Alice calls `LRTDepositPool.depositETH{value: 1 ether}(0, "")`
- `getRsETHAmountToMint(ETH, 1e18)` = `(1e18 × 1e18) / 1.000e18 = 1.000e18` rsETH
- Correct amount at true price: `1e18 / 1.0009e18 ≈ 0.9991e18` rsETH
- Alice receives ~0.09% excess rsETH (~0.0009 rsETH per 1 ETH deposited)

**Step 4 — Existing holders are diluted:**
- New rsETH supply: 10,001 rsETH backing 10,010 ETH
- True price should be 1.0009 ETH/rsETH; actual stored price is 1.000 ETH/rsETH
- Every existing holder's redemption value is reduced by the dilution from Alice's excess mint

**Foundry test plan:** Deploy `LRTOracle` with `maxFeeMintAmountPerDay = 0.5e18`. Simulate reward accrual causing `rsethAmountToMintAsProtocolFee > 0.5e18`. Call `updateRSETHPrice()` and assert it reverts. Assert `rsETHPrice` is unchanged. Call `getRsETHAmountToMint` and assert the returned amount exceeds the amount computed using the correct post-reward price. Assert the difference equals `deposit × (1/stale_price − 1/true_price)`.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L303-303)
```text
            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
