### Title
`LRTDepositPool#getRsETHAmountToMint` Uses Stale Cached `rsETHPrice` That Does Not Reflect Pending Staking Rewards — (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`, a stored state variable that is only updated when `updateRSETHPrice()` is explicitly called. Between updates, the stored price lags behind the true protocol TVL (which grows continuously from staking rewards and LST appreciation). This is the direct analog of `CompoundV2Connector` using `exchangeRateStored()` instead of `exchangeRateCurrent()`: a cached rate is used where the live rate is required, causing share/asset mis-accounting.

---

### Finding Description

`LRTOracle.rsETHPrice` is a state variable written only inside `_updateRsETHPrice()`, which is triggered by the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`. [1](#0-0) 

The deposit path never calls `updateRSETHPrice()` before minting. It reads the stored price directly:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

The live price is computed inside `_updateRsETHPrice()` as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [3](#0-2) 

where `_getTotalEthInProtocol()` sums all assets across the deposit pool, node delegators, EigenLayer strategies, and the unstaking vault: [4](#0-3) 

Between price-update calls, the protocol continuously accrues staking rewards (beacon-chain ETH, stETH rebases, rETH/ETHx appreciation). The stored `rsETHPrice` therefore understates the true per-share value. Because the minting formula is:

```
rsethAmountToMint = depositValue / rsETHPrice
```

a lower-than-actual `rsETHPrice` inflates the number of rsETH minted per unit of ETH deposited.

The L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV2ExternalBridge`, etc.) also read the same stale price through their `getRate()` → `ILRTOracle.rsETHPrice()` path: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**High — Theft of unclaimed yield from existing rsETH holders.**

Concrete arithmetic:

| State | TVL (ETH) | rsETH supply | True price | Stored price |
|---|---|---|---|---|
| Before update | 1 050 | 1 000 | 1.05 | 1.00 (stale) |
| Attacker deposits 100 ETH | 1 150 | 1 100 | — | — |
| After update | 1 150 | 1 100 | 1.0455 | 1.0455 |

- Attacker paid 100 ETH, received 100 rsETH (correct: ≈ 95.24 rsETH). At post-update price their 100 rsETH is worth **104.55 ETH** — a **4.55 ETH profit**.
- Original 1 000 rsETH holders' share of TVL drops from 1 050 ETH to **1 045.5 ETH** — a **4.5 ETH loss of accrued yield**.

The attacker extracts yield that rightfully belongs to existing stakers. The magnitude scales with deposit size and the staleness window.

---

### Likelihood Explanation

**Medium.** `updateRSETHPrice()` is a public function callable by anyone, but it is not called atomically inside `depositETH()` or `depositAsset()`. Any deposit that occurs while the price is stale (which is the normal state between keeper calls) exploits this. The staleness window can span hours or days depending on keeper cadence. No special permissions are required; any depositor benefits automatically.

---

### Recommendation

Call `updateRSETHPrice()` (or an internal equivalent) at the start of `depositETH()` and `depositAsset()` before computing `rsethAmountToMint`, so the price used for minting always reflects the current TVL. Alternatively, compute the live price inline within `getRsETHAmountToMint()` using `_getTotalEthInProtocol()` divided by `rsethSupply`, mirroring the pattern of `exchangeRateCurrent()` in Compound (which calls `accrueInterest()` before returning the rate).

---

### Proof of Concept

1. Observe that staking rewards have accrued since the last `updateRSETHPrice()` call (e.g., beacon-chain ETH rewards, stETH rebase). The stored `rsETHPrice` is now lower than the true price.
2. Call `LRTDepositPool.depositETH{value: X}(0, "")` without first calling `updateRSETHPrice()`.
3. `getRsETHAmountToMint()` computes `rsethAmountToMint = X * 1e18 / rsETHPrice` using the stale (understated) price.
4. The caller receives more rsETH than the current TVL justifies.
5. After `updateRSETHPrice()` is called (by anyone), the new price reflects the diluted TVL, and the attacker's rsETH is worth more than they paid, at the expense of pre-existing holders. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-250)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L307-315)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
