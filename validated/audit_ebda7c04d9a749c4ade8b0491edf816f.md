### Title
`ethValueInWithdrawal` Understated via Price-Appreciation Clamping When Multiple LST Assets Are Held in Converter — (`contracts/LRTConverter.sol`)

---

### Summary

`LRTConverter.transferAssetToDepositPool` uses the **current** oracle price to subtract from `ethValueInWithdrawal`, while `transferAssetFromDepositPool` used the **original** oracle price to add to it. When an LST's price appreciates between the two calls, the subtraction can exceed the original addition, triggering the clamp to zero — even though other LST assets remain in the converter and their ETH value is no longer tracked anywhere.

---

### Finding Description

`transferAssetFromDepositPool` records the ETH value of an incoming asset at the price at time of transfer: [1](#0-0) 

`transferAssetToDepositPool` subtracts the ETH value of the outgoing asset at the **current** price, clamped to zero: [2](#0-1) 

These two operations are not symmetric. If the oracle price of an asset rises between the two calls, `assetValue` at return time exceeds the amount originally added. With two assets in the converter (e.g., stETH and ETHx), the combined `ethValueInWithdrawal = V1 + V2`. If ETHx's price appreciates such that `Y * newPrice / 1e18 > V1 + V2`, the clamp fires and `ethValueInWithdrawal` is set to `0`, even though stETH (worth V1 ETH) is still sitting in the converter.

The stETH in the converter is explicitly excluded from per-asset accounting: [3](#0-2) 

So its value is **only** tracked via `ethValueInWithdrawal`. Once that is zeroed, the stETH value disappears from the TVL calculation entirely until stETH is itself returned to the deposit pool.

`ethValueInWithdrawal` is consumed directly as `ethLyingInConverter` in `getETHDistributionData`: [4](#0-3) 

This feeds `getTotalAssetDeposits(ETH_TOKEN)`: [5](#0-4) 

Which feeds `_getTotalEthInProtocol()` in `LRTOracle`: [6](#0-5) 

Which determines `rsETHPrice`: [7](#0-6) 

---

### Impact Explanation

During the window between the ETHx return and the stETH return, `ethValueInWithdrawal = 0` while stETH worth V1 ETH is in the converter. The protocol TVL is understated by V1, `rsETHPrice` is deflated, and new depositors receive more rsETH per unit deposited than they are entitled to — diluting existing rsETH holders. No funds are lost (the stETH is still present), but the contract fails to deliver the correct exchange rate it promises. This matches **Low: Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

LST prices (stETH, ETHx, etc.) monotonically increase over time as they accrue staking rewards. Any scenario where two LSTs are transferred into the converter and one is returned after a period of price appreciation can trigger this. This is a normal operational flow — no malicious actor is required. The `onlyAssetTransferRole` restriction means only a trusted operator triggers it, but the operator is acting correctly; the bug is in the accounting logic itself.

---

### Recommendation

Track `ethValueInWithdrawal` per-asset rather than as a single aggregate. On `transferAssetFromDepositPool`, record `ethValueByAsset[_asset] += assetValue`. On `transferAssetToDepositPool`, subtract only `ethValueByAsset[_asset]` (clamped per-asset), and recompute `ethValueInWithdrawal` as the sum. This ensures that returning one asset at an appreciated price cannot zero out the tracked value of other assets still held.

Alternatively, recompute `ethValueInWithdrawal` on-the-fly from actual balances and current oracle prices rather than maintaining a running accumulator.

---

### Proof of Concept

```solidity
// Setup: stETH price = 1.0 ETH, ETHx price = 1.0 ETH
// Step 1: transferAssetFromDepositPool(stETH, 100e18)
//   ethValueInWithdrawal += 100e18 * 1.0 / 1e18 = 100e18
//   ethValueInWithdrawal = 100e18

// Step 2: transferAssetFromDepositPool(ETHx, 100e18)
//   ethValueInWithdrawal += 100e18 * 1.0 / 1e18 = 100e18
//   ethValueInWithdrawal = 200e18

// Step 3: ETHx oracle price spikes to 2.1 ETH (e.g., after staking rewards accumulate)

// Step 4: transferAssetToDepositPool(ETHx, 100e18)
//   assetValue = 100e18 * 2.1 / 1e18 = 210e18
//   210e18 > 200e18 → ethValueInWithdrawal = 0  ← CLAMP FIRES

// Step 5: stETH (worth 100e18 ETH) still in converter
//         ethValueInWithdrawal = 0
//         ethLyingInConverter = 0 (understated by 100e18)
//         rsETHPrice deflated by ~100e18 / totalSupply

// Assert: ethValueInWithdrawal (0) < true remaining ETH value (100e18)
```

### Citations

**File:** contracts/LRTConverter.sol (L140-140)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

**File:** contracts/LRTConverter.sol (L160-163)
```text
        uint256 assetValue = (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        // Set to 0 if assetValue exceeds ethValueInWithdrawal, otherwise subtract assetValue
        ethValueInWithdrawal = ethValueInWithdrawal > assetValue ? ethValueInWithdrawal - assetValue : 0;
```

**File:** contracts/LRTDepositPool.sol (L385-396)
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

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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
