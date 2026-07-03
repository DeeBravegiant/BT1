### Title
Stale `rsETHPrice` Allows Theft of Unclaimed ETH Rewards Sitting in `FeeReceiver` - (File: contracts/LRTDepositPool.sol)

### Summary
`getETHDistributionData()` in `LRTDepositPool` explicitly excludes the `FeeReceiver` balance from the protocol's total ETH accounting. Because `rsETHPrice` is a stored value updated only when `updateRSETHPrice()` is called, and because both `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are permissionless, an attacker can deposit at a stale (artificially low) price, then atomically trigger the reward transfer and price update to steal a disproportionate share of accumulated MEV/execution-layer rewards.

### Finding Description

`LRTDepositPool.getETHDistributionData()` explicitly excludes ETH rewards held in the `FeeReceiver` (also called `RewardReceiver`) contract from the protocol's total ETH balance:

```solidity
/// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
/// @dev rewards are not accounted here
/// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
function getETHDistributionData() public view override returns (...) {
    ethLyingInDepositPool = address(this).balance;
    // FeeReceiver balance is never queried here
    ...
}
``` [1](#0-0) 

`getTotalAssetDeposits()` aggregates from `getAssetDistributionData()` / `getETHDistributionData()`, so the FeeReceiver balance is absent from the total. [2](#0-1) 

`getRsETHAmountToMint()` divides by the **stored** `rsETHPrice`, which was last computed without the FeeReceiver balance:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`LRTOracle.updateRSETHPrice()` is a public, permissionless function: [4](#0-3) 

`FeeReceiver.sendFunds()` is also permissionless — any caller can push accumulated rewards into the deposit pool: [5](#0-4) 

`LRTWithdrawalManager.getExpectedAssetAmount()` uses the same `rsETHPrice` for redemption, so a higher post-update price directly translates to more assets on withdrawal: [6](#0-5) 

### Impact Explanation

**High — Theft of unclaimed yield.**

An attacker who deposits while `rsETHPrice` is stale (i.e., before FeeReceiver rewards are flushed) receives more rsETH than the true share of protocol assets warrants. After the attacker triggers `sendFunds()` and `updateRSETHPrice()`, the price rises to reflect the newly included rewards. The attacker's rsETH now redeems for more assets than were deposited, effectively stealing a portion of the accumulated MEV/execution-layer rewards that belonged to existing rsETH holders. The larger the deposit relative to the existing TVL, and the larger the FeeReceiver balance, the greater the theft.

### Likelihood Explanation

**High.** No privileged role is required. The attack requires only:
1. Observing that `FeeReceiver` holds a non-trivial ETH balance (on-chain, publicly visible).
2. Calling `depositETH()` or `depositAsset()` on `LRTDepositPool`.
3. Calling `FeeReceiver.sendFunds()` (permissionless).
4. Calling `LRTOracle.updateRSETHPrice()` (permissionless).
5. Initiating a withdrawal via `LRTWithdrawalManager`.

All steps are callable by any EOA or contract. Flash loans can amplify the deposit size to maximize the stolen fraction of rewards.

### Recommendation

Include the `FeeReceiver` (and any other reward-holding contract) balance in `getETHDistributionData()` so that `getTotalAssetDeposits()` and therefore `rsETHPrice` always reflect the true protocol TVL. Alternatively, require that `FeeReceiver.sendFunds()` is called and `rsETHPrice` is updated atomically before any deposit is processed, or make `sendFunds()` callable only by a privileged role so the timing cannot be exploited.

### Proof of Concept

1. Assume `FeeReceiver` holds `R` ETH in accumulated MEV rewards (not yet flushed). Current `rsETHPrice` = `P` (computed without `R`). Protocol TVL = `T` ETH, rsETH supply = `S`.

2. Attacker calls `LRTDepositPool.depositETH{value: D}(...)`.
   - `getRsETHAmountToMint` computes: `rsethMinted = D * 1e18 / P`
   - Because `P` is stale (does not include `R`), the attacker receives more rsETH than the true share `D / (T + R)` warrants. [3](#0-2) 

3. Attacker calls `FeeReceiver.sendFunds()` — `R` ETH moves into `LRTDepositPool`. [5](#0-4) 

4. Attacker calls `LRTOracle.updateRSETHPrice()` — new price `P' = (T + D + R) / (S + rsethMinted)` > `P`. [4](#0-3) 

5. Attacker requests withdrawal via `LRTWithdrawalManager`. `getExpectedAssetAmount` returns `rsethMinted * P' / assetPrice`, which is greater than `D`, with the surplus coming from the rewards `R` that belonged to pre-existing holders. [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L385-397)
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
