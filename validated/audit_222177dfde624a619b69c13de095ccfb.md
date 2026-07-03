### Title
`LRTOracle._getTotalEthInProtocol()` Excludes Unclaimed ETH Rewards from TVL, Understating rsETH Price - (`contracts/LRTDepositPool.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` computes the rsETH/ETH exchange rate by dividing total protocol ETH by rsETH supply. The total ETH is sourced from `LRTDepositPool.getETHDistributionData()`, which explicitly excludes ETH rewards sitting in the `FeeReceiver`/reward receiver contracts. This causes the rsETH price to be persistently understated between reward collection cycles, allowing new depositors to mint more rsETH than they are entitled to, diluting existing holders.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which iterates over all supported assets and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for each. [1](#0-0) 

For the ETH asset, `getTotalAssetDeposits` delegates to `getETHDistributionData()`: [2](#0-1) 

The function's own NatSpec explicitly acknowledges the omission:

```
/// @dev rewards are not accounted here
/// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
``` [3](#0-2) 

The ETH distribution only counts:
- `address(this).balance` (deposit pool)
- NDC ETH balances
- `getEffectivePodShares()` (EigenLayer beacon chain)
- Queued withdrawal amounts
- Unstaking vault balance
- Converter ETH value

It does **not** count ETH rewards (validator tips, MEV, EigenLayer AVS rewards) sitting in the `FeeReceiver` or reward receiver contracts until they are explicitly moved to the deposit pool.

The rsETH price is then computed as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [4](#0-3) 

And new deposits use this price to determine how much rsETH to mint:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [5](#0-4) 

When `rsETHPrice` is understated (because unclaimed rewards are excluded), the division yields a **larger** `rsethAmountToMint` than the depositor deserves. Once rewards are eventually moved to the deposit pool and `updateRSETHPrice()` is called, the price jumps — but the extra rsETH already minted to the new depositor permanently dilutes all prior holders.

---

### Impact Explanation

Existing rsETH holders lose a portion of their accrued yield. Every time rewards accumulate in `FeeReceiver` without being moved to the deposit pool, the rsETH price is understated. Any deposit made during this window mints excess rsETH, diluting the share of existing holders. This constitutes **theft of unclaimed yield** from existing rsETH holders.

Impact: **High — Theft of unclaimed yield.**

---

### Likelihood Explanation

Rewards (validator tips, MEV, EigenLayer AVS rewards) accrue continuously. The protocol relies on an off-chain keeper to periodically move rewards from `FeeReceiver` to the deposit pool. Between keeper calls, the gap between true TVL and reported TVL grows. Any depositor — including one who monitors the mempool for large reward accumulations — can exploit this window simply by calling `depositETH` or `depositAsset`. No special privileges are required.

Likelihood: **Medium** — occurs naturally every reward cycle; no attacker action beyond a standard deposit is needed.

---

### Recommendation

Include the ETH balance of the `FeeReceiver` (and any other reward receiver contracts) in `getETHDistributionData()` so that accrued-but-uncollected rewards are reflected in the rsETH price at all times. Alternatively, enforce that `updateRSETHPrice()` can only be called after rewards have been swept to the deposit pool, or add a view function on `FeeReceiver` that returns its pending ETH balance and include it in the TVL sum.

---

### Proof of Concept

1. Protocol has 1000 ETH in TVL, 1000 rsETH outstanding → rsETH price = 1.0 ETH.
2. 10 ETH of validator rewards accumulate in `FeeReceiver` (not yet moved to deposit pool).
3. `updateRSETHPrice()` is called → `_getTotalEthInProtocol()` returns 1000 ETH (rewards excluded) → rsETH price remains 1.0 ETH.
4. Attacker deposits 100 ETH → mints `100 / 1.0 = 100 rsETH`. Correct amount would be `100 / (1010/1000) = ~99.01 rsETH`.
5. Keeper moves 10 ETH from `FeeReceiver` to deposit pool. `updateRSETHPrice()` is called → TVL = 1110 ETH, supply = 1100 rsETH → price = ~1.009 ETH.
6. Attacker holds 100 rsETH worth ~100.9 ETH. Original holders' 1000 rsETH is now worth ~1009 ETH instead of the ~1010 ETH they were entitled to. The attacker captured ~0.9 ETH of yield that belonged to prior holders. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L244-250)
```text
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

**File:** contracts/LRTDepositPool.sol (L464-500)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
        ethLyingInDepositPool = address(this).balance;

        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
