### Title
External ETH/LST Donations to LRTDepositPool Misclassified as Protocol Yield, Causing Incorrect Fee Minting — (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._updateRsETHPrice()` computes protocol TVL using raw contract balances (`address(this).balance` and `IERC20.balanceOf()`). Because `LRTDepositPool` has an unrestricted `receive()` fallback and accepts arbitrary ERC20 transfers, any external party can inflate the measured TVL by donating ETH or LSTs directly. The oracle then misclassifies the donation as yield, mints rsETH as protocol fee to the treasury, and dilutes existing rsETH holders — the exact same share/asset mis-accounting class as the reference report.

---

### Finding Description

`LRTOracle._getTotalEthInProtocol()` aggregates TVL by calling `ILRTDepositPool.getTotalAssetDeposits(asset)` for every supported asset. [1](#0-0) 

For ETH, `getTotalAssetDeposits` delegates to `getETHDistributionData()`, which reads the raw native balance of the deposit pool: [2](#0-1) 

For ERC20 LSTs, it reads the raw token balance: [3](#0-2) 

`LRTDepositPool` accepts ETH from any caller via its unrestricted fallback: [4](#0-3) 

Back in `_updateRsETHPrice()`, the inflated TVL is compared against the previous TVL (`rsethSupply × rsETHPrice`). Any surplus is treated as yield, and a protocol fee is minted as rsETH to the treasury: [5](#0-4) [6](#0-5) 

The protocol never distinguishes between genuine staking yield and arbitrary donations. Any ETH or LST sent directly to `LRTDepositPool` (or to any `NodeDelegator`, whose balance is also summed) is indistinguishable from earned yield. [7](#0-6) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When an attacker donates `X` ETH to `LRTDepositPool`, the next call to `updateRSETHPrice()` treats `X` as yield. The protocol mints `(X × protocolFeeInBPS / 10_000) / newRsETHPrice` rsETH to the treasury. This fee is extracted from value that should have accrued entirely to existing rsETH holders. The treasury receives rsETH backed by the fee portion of the donation; rsETH holders receive proportionally less. The attacker loses the donated ETH, but the protocol's fee mechanism is weaponised against its own users.

A secondary effect: if the donation is large enough to push `newRsETHPrice` above `highestRsethPrice × (1 + pricePercentageLimit)`, non-manager callers cannot invoke `updateRSETHPrice()` until a manager intervenes, temporarily freezing the price oracle. [8](#0-7) 

---

### Likelihood Explanation

**Medium.** Sending ETH to `LRTDepositPool.receive()` requires no permission and no special tooling — a plain ETH transfer suffices. ERC20 donations require only a standard `transfer()` call. The attack is cheap to attempt at small scale and scales linearly with the donated amount. The attacker loses the donated funds, which limits rational economic motivation, but a griefing actor or a competitor can execute this at any time without any protocol-side precondition.

---

### Recommendation

Track deposited amounts in explicit accounting variables rather than relying on raw balances. For ETH, maintain a `totalETHDeposited` counter incremented only through the authorised deposit paths (`depositETH`, `receiveFromRewardReceiver`, `receiveFromLRTConverter`, `receiveFromNodeDelegator`) and use that counter instead of `address(this).balance` in `getETHDistributionData()`. Apply the same pattern for ERC20 LSTs. This mirrors the fix recommended in the reference report: track the protocol's own credited balance rather than the total balance of the container.

---

### Proof of Concept

1. Attacker calls `LRTDepositPool.receive()` with 1 000 ETH (plain ETH transfer).
2. `address(LRTDepositPool).balance` increases by 1 000 ETH.
3. Anyone calls `LRTOracle.updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` → `getTotalAssetDeposits(ETH_TOKEN)` → `getETHDistributionData()` returns `ethLyingInDepositPool` inflated by 1 000 ETH.
5. `totalETHInProtocol > previousTVL` by 1 000 ETH; `rewardAmount = 1 000 ETH`.
6. With `protocolFeeInBPS = 1000` (10 %), `protocolFeeInETH = 100 ETH`.
7. Protocol mints `100 ETH / newRsETHPrice` rsETH to treasury — fee extracted from a donation that should have gone entirely to rsETH holders.
8. `rsETHPrice` rises, but by less than it would have without the fee, diluting existing holders by the fee amount. [4](#0-3) [9](#0-8) [10](#0-9) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L252-265)
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
```

**File:** contracts/LRTOracle.sol (L299-307)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
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

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L384-397)
```text
    /// @return totalAssetDeposit total asset present in protocol
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

**File:** contracts/LRTDepositPool.sol (L444-448)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));

        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
```

**File:** contracts/LRTDepositPool.sol (L480-486)
```text
        ethLyingInDepositPool = address(this).balance;

        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

```
