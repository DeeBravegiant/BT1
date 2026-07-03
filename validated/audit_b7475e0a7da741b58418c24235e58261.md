### Title
No Incentive to Call `FeeReceiver.sendFunds()` Enables Yield Dilution of Existing rsETH Holders — (File: contracts/FeeReceiver.sol)

---

### Summary
`FeeReceiver.sendFunds()` is a permissionless function with no caller incentive. MEV/execution-layer rewards accumulate in `FeeReceiver` without being reflected in the protocol's TVL. Because `LRTOracle` prices rsETH using only the TVL tracked by `LRTDepositPool` (which explicitly excludes `FeeReceiver` balances), the rsETH price is understated for as long as rewards sit uncollected. Any new deposit made during this window mints excess rsETH, permanently diluting the yield owed to existing rsETH holders.

---

### Finding Description

`FeeReceiver` receives MEV and execution-layer rewards as ETH. The only mechanism to move those rewards into the protocol's TVL is `sendFunds()`:

```solidity
// contracts/FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

The function has **no access control** and **no caller reward**. There is zero on-chain incentive for any external party to call it.

`LRTDepositPool.getETHDistributionData()` explicitly excludes `FeeReceiver` from TVL accounting:

```solidity
// contracts/LRTDepositPool.sol L465-466
/// @dev rewards are not accounted here
/// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
```

`LRTOracle._getTotalEthInProtocol()` computes the rsETH price by summing `getTotalAssetDeposits()` across all supported assets:

```solidity
// contracts/LRTOracle.sol L341-343
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

Because `FeeReceiver`'s balance is never included in `getTotalAssetDeposits()` until `sendFunds()` is called, `rsETHPrice` is understated by the full amount of uncollected rewards. `LRTDepositPool.getRsETHAmountToMint()` uses this stale price:

```solidity
// contracts/LRTDepositPool.sol L519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

A lower `rsETHPrice` means more rsETH is minted per unit of ETH deposited. Every new deposit made while rewards sit in `FeeReceiver` mints excess rsETH, permanently diluting the share of the reward pool that belongs to existing holders.

**Concrete example:**
- TVL = 1 000 ETH, supply = 1 000 rsETH → price = 1.000 ETH/rsETH
- 10 ETH MEV rewards accumulate in `FeeReceiver` (not counted)
- Attacker/user deposits 100 ETH → receives 100 rsETH (price still 1.000)
- `sendFunds()` is called → TVL = 1 110 ETH, supply = 1 100 rsETH → price = 1.009 ETH/rsETH

Had `sendFunds()` been called first:
- Price = 1.010 ETH/rsETH → depositor receives only 99.01 rsETH
- Final price = 1.010 ETH/rsETH

Existing holders lose ≈ 0.001 ETH per rsETH (≈ 0.1 % of their accrued yield) to the late depositor. The loss scales with the ratio of uncollected rewards to TVL and the volume of deposits during the delay window.

---

### Impact Explanation
**High — Theft of unclaimed yield.**
Existing rsETH holders permanently lose a portion of MEV/execution-layer yield to depositors who enter while rewards are stranded in `FeeReceiver`. The dilution is irreversible once the excess rsETH is minted. The magnitude scales with the size of accumulated rewards and the deposit volume during the delay.

---

### Likelihood Explanation
**Low.**
The protocol team is expected to call `sendFunds()` periodically off-chain. However, there is no on-chain enforcement, no keeper incentive, and no minimum call frequency. During periods of high MEV (e.g., around major on-chain events), rewards can accumulate rapidly. A sophisticated depositor who monitors `FeeReceiver.balance` can time a large deposit to exploit the window before `sendFunds()` is called.

---

### Recommendation
1. **Caller incentive:** Reward the `sendFunds()` caller with a small fixed percentage (e.g., 0.1–0.5 %) of the forwarded balance, analogous to liquidation bonuses in lending protocols.
2. **Auto-forward on deposit:** Call `sendFunds()` (or an equivalent internal flush) at the start of `LRTDepositPool.depositETH()` / `depositAsset()` so rewards are always counted before new rsETH is minted.
3. **Keeper automation:** Deploy a Gelato/Chainlink Automation keeper that calls `sendFunds()` whenever `FeeReceiver.balance` exceeds a configurable threshold, funded by a portion of the rewards.

---

### Proof of Concept

1. Observe `FeeReceiver.balance` growing (MEV rewards arriving continuously).
2. Wait until a meaningful amount accumulates (e.g., 10 ETH on a 1 000 ETH TVL base).
3. Call `LRTOracle.updateRSETHPrice()` — price is computed without the 10 ETH, so it is understated.
4. Call `LRTDepositPool.depositETH{value: 100 ether}(0, "")` — receive `100 / understatedPrice` rsETH, which is more than the fair amount.
5. Call `FeeReceiver.sendFunds()` — 10 ETH enters TVL.
6. Call `LRTOracle.updateRSETHPrice()` — price rises, but the excess rsETH minted in step 4 permanently dilutes existing holders' share of the 10 ETH reward.

Key code references: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L464-466)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
```

**File:** contracts/LRTDepositPool.sol (L517-521)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L331-348)
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
```
