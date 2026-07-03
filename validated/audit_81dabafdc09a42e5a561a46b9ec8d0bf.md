### Title
rsETH Price Understated Due to Unaccounted FeeReceiver Rewards, Enabling Sandwich Attack on Minting - (File: `contracts/LRTOracle.sol`, `contracts/FeeReceiver.sol`)

---

### Summary

`LRTOracle._getTotalEthInProtocol()` deliberately excludes ETH rewards sitting in `FeeReceiver`, causing `rsETHPrice` to be understated relative to the protocol's true TVL. Because `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are both permissionless, an attacker can sandwich the reward settlement: deposit at the understated price, trigger the reward flush and price update, then hold rsETH that is worth more than what was paid — at the expense of existing rsETH holders.

---

### Finding Description

`LRTOracle._getTotalEthInProtocol()` computes the protocol's total ETH value by iterating over supported assets and calling `ILRTDepositPool.getTotalAssetDeposits(asset)` for each: [1](#0-0) 

For ETH, `getTotalAssetDeposits` delegates to `getETHDistributionData()`, which explicitly documents that rewards in `FeeReceiver` are **not** included: [2](#0-1) 

The `FeeReceiver` contract accumulates MEV and execution-layer rewards passively via its `receive()` fallback. Its `sendFunds()` function, which moves all accumulated ETH to the deposit pool, is **permissionless**: [3](#0-2) 

The stored `rsETHPrice` is used directly in the minting formula: [4](#0-3) 

And `updateRSETHPrice()` is also permissionless: [5](#0-4) 

**Attack sequence:**

1. FeeReceiver accumulates `F` ETH in rewards. Protocol's tracked TVL is `T`, rsETH supply is `S`, so stored `rsETHPrice = T / S` (understated; true price = `(T + F) / S`).
2. Attacker calls `LRTDepositPool.depositETH()` with `X` ETH. Minted rsETH = `X * S / T` — more than the fair amount `X * S / (T + F)`.
3. Attacker calls `FeeReceiver.sendFunds()` → `F` ETH moves to deposit pool, TVL becomes `T + X + F`.
4. Attacker calls `LRTOracle.updateRSETHPrice()` → price updates to `(T + X + F) / (S + X*S/T)`.
5. Attacker's rsETH is now worth more than `X` ETH. The surplus `≈ F * X / (T + X)` is extracted from existing holders' yield.

---

### Impact Explanation

Existing rsETH holders are entitled to the MEV/execution-layer rewards accumulated in `FeeReceiver`. By depositing at the understated price just before the reward flush, an attacker captures a portion of those rewards proportional to their deposit size relative to total TVL. This is a direct theft of unclaimed yield from existing holders.

**Impact: High — Theft of unclaimed yield.**

---

### Likelihood Explanation

- `FeeReceiver.sendFunds()` and `updateRSETHPrice()` are both callable by any address with no restrictions.
- `FeeReceiver` accumulates rewards continuously from validator MEV and execution-layer tips; the gap between accumulation and settlement is a normal operational condition, not an edge case.
- The attack requires no special privileges, no flash loans, and no oracle manipulation — only two permissionless calls.
- The only partial mitigation is `pricePercentageLimit`, which reverts `updateRSETHPrice()` for non-managers if the price jump exceeds the configured threshold. However, if `pricePercentageLimit == 0` (no limit set), or if accumulated rewards are small relative to TVL, this check does not trigger. [6](#0-5) 

**Likelihood: Medium** (requires rewards to have accumulated and the price jump to be within the configured threshold).

---

### Recommendation

Include the `FeeReceiver` balance in `_getTotalEthInProtocol()` (or equivalently in `getETHDistributionData()`), so that `rsETHPrice` always reflects the true protocol TVL including unsettled rewards. This eliminates the gap between the "real" price and the price used for minting:

```solidity
// In getETHDistributionData() or _getTotalEthInProtocol():
address feeReceiver = lrtConfig.getContract(LRTConstants.LRT_FEE_RECEIVER);
ethLyingInFeeReceiver = feeReceiver.balance;
```

Alternatively, make `sendFunds()` access-restricted so that reward settlement is controlled and cannot be triggered atomically by an attacker.

---

### Proof of Concept

```
State before attack:
  FeeReceiver balance (F) = 10 ETH (accumulated MEV rewards)
  Deposit pool tracked TVL (T) = 1000 ETH
  rsETH supply (S) = 1000 rsETH
  Stored rsETHPrice = 1000/1000 = 1.0 ETH/rsETH
  True rsETHPrice = 1010/1000 = 1.01 ETH/rsETH

Step 1 — Attacker deposits 100 ETH:
  rsETH minted = 100 / 1.0 = 100 rsETH  (fair would be 100/1.01 ≈ 99.01 rsETH)

Step 2 — Attacker calls FeeReceiver.sendFunds():
  Deposit pool ETH = 1000 + 100 + 10 = 1110 ETH

Step 3 — Attacker calls updateRSETHPrice():
  New rsETH supply = 1000 + 100 = 1100 rsETH
  New rsETHPrice = 1110 / 1100 ≈ 1.00909 ETH/rsETH

Attacker's 100 rsETH is now worth 100 * 1.00909 = 100.909 ETH
Attacker paid 100 ETH → profit ≈ 0.909 ETH stolen from existing holders' MEV yield
```

The stolen amount scales with `F * X / (T + X)`. For large deposits or large accumulated rewards, the theft is material.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
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
            }
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

**File:** contracts/FeeReceiver.sol (L52-58)
```text
    /// @dev send all rewards to deposit pool
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```
