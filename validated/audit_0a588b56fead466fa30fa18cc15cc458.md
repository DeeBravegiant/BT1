### Title
Pending Withdrawal Burns Cause Spurious Protocol Fee Minting in `_updateRsETHPrice()` — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` approximates the previous TVL as `currentRsethSupply × storedRsETHPrice`. When rsETH is burned at withdrawal initiation but the underlying ETH remains in NodeDelegators during the EigenLayer withdrawal delay, the current supply is lower than at the last price update while `totalETHInProtocol` is unchanged. The oracle interprets this supply-TVL divergence as yield growth and mints excess protocol fees to the treasury, diluting existing rsETH holders.

---

### Finding Description

`_updateRsETHPrice()` computes the "previous TVL" as:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

where `rsethSupply` is the **current** L1 rsETH `totalSupply()` and `rsETHPrice` is the **stored** price from the last update. [1](#0-0) 

The stored price was set at the last update as `totalETHInProtocol_last / rsethSupply_last`, so:

```
previousTVL = rsethSupply_current × (totalETHInProtocol_last / rsethSupply_last)
```

This is only correct when `rsethSupply_current == rsethSupply_last`. When rsETH is burned between updates (withdrawal initiation), `rsethSupply_current < rsethSupply_last`, so `previousTVL` is understated relative to the actual previous TVL.

`_getTotalEthInProtocol()` sums assets across all NodeDelegators via `ILRTDepositPool.getTotalAssetDeposits()`. ETH that is pending an EigenLayer withdrawal is still held in NodeDelegators and is still counted in `totalETHInProtocol`. [2](#0-1) 

The fee-minting branch then fires:

```solidity
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
``` [3](#0-2) 

The spurious `rewardAmount` equals approximately `burnedRsETH × rsETHPrice` — the full ETH value of the burned tokens — and a fraction of that is minted as rsETH to the treasury:

```solidity
IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
``` [4](#0-3) 

`updateRSETHPrice()` is a public, permissionless function callable by anyone: [5](#0-4) 

---

### Impact Explanation

**Theft of unclaimed yield.** Excess rsETH is minted to the treasury at the expense of existing rsETH holders. For every withdrawal in the EigenLayer queue, the oracle over-counts yield by `burnedRsETH × rsETHPrice × protocolFeeRate`. With a 10% fee rate and 1 000 ETH of pending withdrawals, ~100 ETH worth of rsETH is incorrectly minted to the treasury, permanently diluting all other holders.

---

### Likelihood Explanation

EigenLayer enforces a multi-day withdrawal delay. During this window, rsETH is burned but the underlying ETH remains in NodeDelegators. `updateRSETHPrice()` is public and is expected to be called regularly (e.g., by keepers). Any withdrawal of meaningful size triggers the condition. This is a normal, expected protocol operation, not an edge case.

---

### Recommendation

Track the actual TVL at the time of the last price update in a storage variable (e.g., `lastRecordedTVL`) and use that instead of `rsethSupply × rsETHPrice` to compute `previousTVL`. Alternatively, subtract the ETH value of all pending (queued but unclaimed) withdrawals from `totalETHInProtocol` before comparing it to `previousTVL`, so that burned rsETH and its corresponding ETH are excluded symmetrically.

---

### Proof of Concept

**Setup:** 10 000 rsETH outstanding, `rsETHPrice = 1.05 ETH`, `totalETHInProtocol = 10 500 ETH`, `protocolFeeInBPS = 1000` (10%).

1. A user initiates a withdrawal of 1 000 rsETH. rsETH is burned; `rsethSupply` drops to 9 000. The 1 050 ETH backing those tokens remains in NodeDelegators (EigenLayer delay).

2. Anyone calls `updateRSETHPrice()`.

3. Oracle computes:
   - `previousTVL = 9 000 × 1.05 = 9 450 ETH`
   - `totalETHInProtocol = 10 500 ETH` (unchanged)
   - `rewardAmount = 10 500 − 9 450 = 1 050 ETH` ← spurious
   - `protocolFeeInETH = 1 050 × 10% = 105 ETH`

4. `newRsETHPrice = (10 500 − 105) / 9 000 ≈ 1.155 ETH`

5. `rsethAmountToMintAsProtocolFee = 105 / 1.155 ≈ 90.9 rsETH` minted to treasury.

The treasury receives ~90.9 rsETH it is not entitled to. All 9 000 remaining rsETH holders are diluted by ~1%.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L216-234)
```text
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L304-307)
```text
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
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
