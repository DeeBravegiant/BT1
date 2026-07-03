### Title
Permissionless `FeeReceiver.sendFunds()` Allows New Depositors to Dilute Existing Holders' Accumulated MEV Rewards - (File: contracts/FeeReceiver.sol)

### Summary
`FeeReceiver.sendFunds()` carries no access control, and the ETH balance sitting in `FeeReceiver` is explicitly excluded from the TVL used to price rsETH. Any external caller can deposit into `LRTDepositPool` at a price that does not reflect accumulated MEV/execution-layer rewards, then trigger `sendFunds()` to move those rewards into the TVL, causing the rsETH price to rise and diluting existing holders' unclaimed yield.

### Finding Description
MEV and execution-layer rewards accumulate in `FeeReceiver`. The `LRTDepositPool.getETHDistributionData()` function explicitly excludes the `FeeReceiver` balance from TVL:

> "rewards are not accounted here, it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool" [1](#0-0) 

The rsETH mint price is derived from the stored `LRTOracle.rsETHPrice`, which is computed over this same TVL: [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` divides by the stored `rsETHPrice`: [3](#0-2) 

`FeeReceiver.sendFunds()` has **no access control modifier**: [4](#0-3) 

`LRTOracle.updateRSETHPrice()` is also permissionless: [5](#0-4) 

An attacker can therefore:
1. Observe rewards R accumulating in `FeeReceiver` (not in TVL).
2. Deposit ETH at the current (lower) rsETHPrice P, receiving `A / P` rsETH.
3. Call `FeeReceiver.sendFunds()` → R ETH moves into `LRTDepositPool`, increasing TVL.
4. Call `LRTOracle.updateRSETHPrice()` → new price P′ = (TVL + R) / totalSupply > P.
5. The attacker's rsETH is now worth `(A / P) × P′ > A`.

The attacker captures `A × R / TVL` ETH of value that rightfully belongs to existing rsETH holders.

### Impact Explanation
**High — Theft of unclaimed yield.** Existing rsETH holders accumulate MEV/execution-layer rewards over time. Because `FeeReceiver` balance is excluded from TVL until `sendFunds()` is called, a new depositor who enters just before that call receives rsETH priced as if those rewards do not exist, then immediately benefits from the price increase when the rewards are flushed in. The yield that accrued to existing holders is permanently diluted proportional to the attacker's deposit size relative to total TVL.

### Likelihood Explanation
**Low/Medium.** The attack requires rewards to accumulate in `FeeReceiver` between calls to `sendFunds()`. Because `sendFunds()` is permissionless, the attacker fully controls the timing of the flush. The attacker can watch on-chain for the `FeeReceiver` balance to grow to a profitable threshold, deposit, then immediately call `sendFunds()` and `updateRSETHPrice()` in the same block or across two blocks. No privileged access is required.

### Recommendation
Restrict `FeeReceiver.sendFunds()` to a trusted role (e.g., `MANAGER`) so that the timing of reward distribution cannot be weaponized by an external caller:

```solidity
// contracts/FeeReceiver.sol
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Alternatively, integrate the `FeeReceiver` balance into the TVL calculation inside `LRTDepositPool.getETHDistributionData()` so that accumulated rewards are always reflected in the rsETH price, eliminating the pricing gap that makes the attack profitable.

### Proof of Concept

**Setup:**
- Total TVL = 1000 ETH, rsETH totalSupply = 1000, rsETHPrice = 1.0 ETH/rsETH.
- FeeReceiver holds 10 ETH in accumulated MEV rewards (not in TVL).

**Attack:**
- `t=0`: Attacker deposits 100 ETH → receives `100 / 1.0 = 100 rsETH`.
- `t=0`: Attacker calls `FeeReceiver.sendFunds()` → 10 ETH moves to DepositPool. TVL = 1110 ETH, totalSupply = 1100 rsETH.
- `t=0`: Attacker calls `LRTOracle.updateRSETHPrice()` → new price = 1110 / 1100 ≈ 1.00909 ETH/rsETH.
- Attacker's 100 rsETH is now worth ≈ 100.909 ETH — a gain of ~0.909 ETH.

**Without the attack:**
- Existing holders' 1000 rsETH would have appreciated by the full 10 ETH reward (price → 1010/1000 = 1.01).
- With the attack, existing holders' 1000 rsETH is worth 1000 × 1.00909 ≈ 1009.09 ETH — they lost ~0.909 ETH of yield to the attacker.

### Citations

**File:** contracts/LRTDepositPool.sol (L464-467)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
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

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```
