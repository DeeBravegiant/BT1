### Title
Stale ETH Value Accounting in `LRTConverter` Causes rsETH Price Mis-Calculation, Diluting Existing Holders — (File: `contracts/LRTConverter.sol`)

---

### Summary

When LST assets (e.g., stETH) are transferred to `LRTConverter` for unstaking, their ETH value is snapshotted at the price at the moment of transfer and stored in `ethValueInWithdrawal`. This value is then used directly by `LRTDepositPool.getETHDistributionData()` and ultimately by `LRTOracle._getTotalEthInProtocol()` to compute the rsETH price. Because LST prices naturally appreciate over time (staking rewards), the snapshot becomes stale, causing the protocol's TVL — and therefore `rsETHPrice` — to be systematically understated for the entire duration assets remain in the converter. New depositors receive more rsETH than they are entitled to, diluting existing holders' yield.

---

### Finding Description

`LRTConverter.transferAssetFromDepositPool` records the ETH value of transferred assets at the current oracle price:

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
``` [1](#0-0) 

This value is never updated to reflect subsequent price changes. `LRTDepositPool.getETHDistributionData()` reads it directly:

```solidity
address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
``` [2](#0-1) 

`LRTOracle._getTotalEthInProtocol()` then aggregates this stale value alongside current prices for all other asset locations:

```solidity
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

For non-ETH assets, `getAssetDistributionData` explicitly zeroes out the converter contribution:

```solidity
assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
``` [4](#0-3) 

This means the stETH sitting in the converter is **exclusively** accounted for via the stale `ethValueInWithdrawal` snapshot. As stETH is a rebasing token, its balance in the converter grows over time, and its ETH exchange rate also increases with staking rewards. Both effects cause the actual ETH value of converter assets to exceed `ethValueInWithdrawal`, understating the protocol's true TVL.

The computed rsETH price is:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [5](#0-4) 

With an understated `totalETHInProtocol`, `newRsETHPrice` is lower than the true value. New depositors then mint rsETH using this deflated price:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [6](#0-5) 

They receive more rsETH than they are entitled to, diluting the claims of existing holders.

The divergence is not corrected until `_sendEthToDepositPool` is called after the Lido withdrawal completes:

```solidity
if (ethValueInWithdrawal > _amount) {
    ethValueInWithdrawal -= _amount;
} else {
    ethValueInWithdrawal = 0;
}
``` [7](#0-6) 

The Lido withdrawal queue typically takes 1–14 days, during which the stale accounting persists.

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

Existing rsETH holders continuously lose yield proportional to the price appreciation of assets held in the converter. stETH accrues approximately 4% APY in staking rewards. Over a 10-day withdrawal queue period, the ETH value of converter assets grows by ~0.11%. For a protocol with 10,000 stETH in the converter (≈10,000 ETH), the TVL understatement is ~11 ETH. New depositors capture this yield by minting rsETH at the deflated price, permanently diluting existing holders' share of the protocol's assets.

---

### Likelihood Explanation

**Likelihood: Medium.**

The `transferAssetFromDepositPool` function is called as part of normal protocol operations whenever stETH needs to be unstaked via Lido. The Lido withdrawal queue routinely takes multiple days. During this entire window, the stale accounting is active and `updateRSETHPrice()` — a public, permissionless function — can be called by anyone (including a depositor seeking to exploit the deflated price). No malicious operator action is required; the divergence arises from ordinary market price appreciation of a rebasing LST.

---

### Recommendation

Replace the fixed ETH value snapshot in `ethValueInWithdrawal` with tracking of the raw asset amount in the converter. When `_getTotalEthInProtocol()` is computed, calculate the ETH value of converter assets using the **current** oracle price rather than the price at the time of transfer. Alternatively, expose a view function from `LRTConverter` that returns the current ETH value of held assets using live oracle prices, and have `getETHDistributionData()` call that instead of reading the stale `ethValueInWithdrawal`.

---

### Proof of Concept

1. Operator calls `transferAssetFromDepositPool(stETH, 10_000e18)` when stETH price is `1.05e18`.
   - `ethValueInWithdrawal = 10_000e18 * 1.05e18 / 1e18 = 10_500e18` (ETH)
   - stETH is removed from deposit pool accounting; converter holds 10,000 stETH.

2. 10 days pass. stETH rebases; converter now holds `10_000 * 1.0011 ≈ 10_011` stETH. stETH price rises to `1.0511e18`.
   - Actual ETH value of converter assets: `10_011 * 1.0511 ≈ 10_522 ETH`
   - `ethValueInWithdrawal` still = `10_500 ETH` (stale, understated by ~22 ETH)

3. Anyone calls `updateRSETHPrice()`. `_getTotalEthInProtocol()` uses `ethValueInWithdrawal = 10_500` instead of the true `10_522`, understating TVL by 22 ETH.

4. `newRsETHPrice` is computed lower than the true value. A new depositor calls `depositETH` and receives more rsETH than they are entitled to at the expense of existing holders.

5. When `claimStEth` is eventually called, `_sendEthToDepositPool(10_522 ETH)` is invoked. Since `10_522 > 10_500`, `ethValueInWithdrawal` is set to 0 and the 22 ETH of yield has already been captured by the new depositor — the dilution is permanent.

### Citations

**File:** contracts/LRTConverter.sol (L140-140)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

**File:** contracts/LRTConverter.sol (L255-259)
```text
        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
```

**File:** contracts/LRTDepositPool.sol (L460-460)
```text
        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
