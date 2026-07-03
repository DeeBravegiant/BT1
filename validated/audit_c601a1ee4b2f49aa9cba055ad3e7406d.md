### Title
`SfrxETHPriceOracle` Uses `pricePerShare()` That Does Not Reflect Unreported Validator Losses, Enabling Over-Minting of rsETH at Existing Holders' Expense - (File: contracts/oracles/SfrxETHPriceOracle.sol)

---

### Summary

`SfrxETHPriceOracle` prices sfrxETH by calling `ISfrxETH.pricePerShare()` directly. Like Yearn's yToken `pricePerShare`, this value reflects only the current frxETH balance of the vault divided by total sfrxETH supply. It does **not** reflect unreported validator slashing losses until those losses are explicitly accounted for on-chain. An attacker who observes a pending but unreported Frax validator slashing can deposit sfrxETH at the inflated rate, receive excess rsETH, and force existing rsETH holders to absorb the loss when the price corrects.

---

### Finding Description

`SfrxETHPriceOracle.getAssetPrice()` returns `ISfrxETH(sfrxETHContractAddress).pricePerShare()` unconditionally:

```solidity
// contracts/oracles/SfrxETHPriceOracle.sol:35-41
function getAssetPrice(address asset) external view returns (uint256) {
    if (asset != sfrxETHContractAddress) {
        revert InvalidAsset();
    }
    return ISfrxETH(sfrxETHContractAddress).pricePerShare();
}
```

This price feeds into two critical protocol paths:

**Path 1 — rsETH minting:**
`LRTDepositPool.depositAsset()` → `getRsETHAmountToMint()` → `lrtOracle.getAssetPrice(sfrxETH) / lrtOracle.rsETHPrice()`. A depositor who deposits sfrxETH when `pricePerShare()` is inflated (pre-loss-report) receives more rsETH than the actual ETH value of their deposit.

**Path 2 — rsETH price update:**
`LRTOracle._updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(sfrxETH)` for each supported asset. The inflated sfrxETH price inflates the computed TVL, which inflates `rsETHPrice`, which causes subsequent depositors to receive fewer rsETH tokens than they should.

The sfrxETH vault (`pricePerShare`) reflects `totalAssets / totalSupply` where `totalAssets` is the frxETH balance held by the vault. When a Frax validator is slashed at the consensus layer, the ETH is burned/lost, but the frxETH balance in the sfrxETH vault does not decrease until the loss is explicitly reported and accounted for on-chain. During this window, `pricePerShare()` remains at its pre-slash value — an overstatement of the true redemption value.

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield / dilution of existing rsETH holders.**

When an attacker deposits sfrxETH at the inflated `pricePerShare()`:
- They receive `rsETHAmount = (depositAmount × inflatedPrice) / rsETHPrice` rsETH tokens
- The actual ETH value of their deposit is lower than what the oracle reports
- When `updateRSETHPrice()` is next called after the loss is reported to the sfrxETH vault, `_getTotalEthInProtocol()` returns a lower value, `rsETHPrice` drops, and all existing rsETH holders bear the loss proportionally
- The attacker has extracted value from the pool at the expense of honest depositors

If the slashing is large enough relative to the sfrxETH TVL in the protocol, this can escalate to **Critical — protocol insolvency**, where the rsETH price drop triggers the downside-protection pause in `_updateRsETHPrice()` (lines 270–281), freezing the protocol.

---

### Likelihood Explanation

**Likelihood: Low-to-Medium.**

Frax validator slashings are rare but have occurred on Ethereum mainnet. The exploitable window is the time between a slashing event at the consensus layer and the moment the sfrxETH vault's `totalAssets` is updated to reflect the loss. An attacker with MEV infrastructure or off-chain monitoring of the beacon chain can detect this window and act within it. No privileged access is required — `LRTDepositPool.depositAsset()` is a public, permissionless function.

---

### Recommendation

1. **Short term:** Do not rely solely on `pricePerShare()` for sfrxETH valuation. Apply a conservative discount or use a secondary Chainlink feed for sfrxETH/ETH to cross-validate the on-chain vault rate before accepting it as the canonical price.

2. **Long term:** Implement a circuit-breaker in `SfrxETHPriceOracle` that compares the current `pricePerShare()` against a time-weighted or externally validated reference rate. If the deviation exceeds a threshold, revert or cap the reported price. This mirrors the `pricePercentageLimit` guard already present in `LRTOracle._updateRsETHPrice()` but applied at the asset oracle level.

---

### Proof of Concept

1. Frax validator is slashed at the consensus layer. The sfrxETH vault's `pricePerShare()` has not yet been updated (still reflects pre-slash value, e.g., `1.05e18`).

2. Attacker calls `LRTDepositPool.depositAsset(sfrxETH, 1000e18, minRSETH, "")`.

3. `getRsETHAmountToMint(sfrxETH, 1000e18)` computes:
   ```
   rsethAmountToMint = (1000e18 × 1.05e18) / rsETHPrice
   ```
   using the inflated `pricePerShare()` via `SfrxETHPriceOracle.getAssetPrice()`.

4. Attacker receives excess rsETH proportional to the unreported loss.

5. The sfrxETH vault reports the slashing loss; `pricePerShare()` drops to, e.g., `1.02e18`.

6. `updateRSETHPrice()` is called. `_getTotalEthInProtocol()` now returns a lower value. `rsETHPrice` drops. All existing rsETH holders' positions are worth less. The attacker's excess rsETH was minted at the expense of honest holders.

**Relevant code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/oracles/SfrxETHPriceOracle.sol (L35-41)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != sfrxETHContractAddress) {
            revert InvalidAsset();
        }

        return ISfrxETH(sfrxETHContractAddress).pricePerShare();
    }
```

**File:** contracts/LRTOracle.sol (L230-251)
```text
        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L331-349)
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
    }
```

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L589-594)
```text
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
