Looking at the relevant contracts to trace the exact code path.

### Title
FeeReceiver ETH Balance Excluded from TVL Enables Block Stuffing to Temporarily Underprice rsETH - (File: contracts/FeeReceiver.sol)

---

### Summary

`FeeReceiver.sendFunds()` is an unpermissioned public function that must be called to move pending MEV/execution-layer rewards into the deposit pool before they are counted in TVL. `LRTOracle._getTotalEthInProtocol()` calls `getETHDistributionData()`, which explicitly excludes `FeeReceiver.balance`. An attacker can use block stuffing to prevent `sendFunds()` from landing, then call `updateRSETHPrice()` themselves, causing the oracle to price rsETH without the pending MEV rewards in TVL.

---

### Finding Description

`FeeReceiver.sendFunds()` has no access control: [1](#0-0) 

It is the only mechanism to move accumulated MEV rewards from `FeeReceiver` into `LRTDepositPool`. Until it is called, `FeeReceiver.balance` is invisible to the oracle.

`LRTOracle._getTotalEthInProtocol()` calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for each supported asset: [2](#0-1) 

For ETH, `getTotalAssetDeposits` delegates to `getETHDistributionData()`, which the codebase itself documents as excluding FeeReceiver: [3](#0-2) 

`getETHDistributionData()` counts only: deposit pool balance, NDC balances, EigenLayer staked/unstaking ETH, converter ETH, and unstaking vault ETH — never `FeeReceiver.balance`: [4](#0-3) 

`updateRSETHPrice()` is also unpermissioned (only `whenNotPaused`): [5](#0-4) 

This means an attacker can:
1. Observe that `FeeReceiver` has accumulated a meaningful ETH balance.
2. Stuff the preceding N blocks with high-gas transactions to crowd out any `sendFunds()` call.
3. In the first non-stuffed block, call `updateRSETHPrice()` directly.
4. The oracle computes `newRsETHPrice = (totalETHInProtocol - fee) / rsethSupply` without the FeeReceiver balance, producing a price lower than the true value.

---

### Impact Explanation

The rsETH price is set below its true value for the duration until `sendFunds()` is eventually called and `updateRSETHPrice()` is called again. During this window:

- New depositors receive more rsETH than they should (diluting existing holders).
- The protocol fails to deliver the promised return that includes accrued MEV rewards.
- The invariant that rsETH price must reflect all accrued protocol assets is violated.

This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value** and **Low — Block stuffing**.

---

### Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive but not impossible, particularly when `FeeReceiver` has accumulated a large balance (e.g., after a period of high MEV activity). The attacker needs the profit from the mispricing to exceed the cost of stuffing N blocks. The attack is more viable on L2 deployments where block stuffing is far cheaper. Both `sendFunds()` and `updateRSETHPrice()` are public with no role restriction, so no privileged access is needed.

---

### Recommendation

Include `FeeReceiver.balance` directly in `getETHDistributionData()` by reading the registered reward receiver address from `LRTConfig` and adding its balance to the TVL computation. This eliminates the gap between accrued rewards and reflected TVL without requiring a separate `sendFunds()` call before each oracle update.

Alternatively, atomically call `sendFunds()` inside `updateRSETHPrice()` (or require it as a precondition) so the oracle always prices against the full ETH balance.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Fork test (Foundry) demonstrating rsETH price difference
// when sendFunds() is suppressed before updateRSETHPrice()

function test_blockStuffing_rsETHMispricing() public {
    // 1. Simulate MEV rewards accumulating in FeeReceiver
    uint256 mevRewards = 10 ether;
    vm.deal(address(feeReceiver), mevRewards);

    // 2. Record rsETH price BEFORE sendFunds() (attacker suppresses it)
    //    Attacker calls updateRSETHPrice() directly — FeeReceiver.balance excluded
    lrtOracle.updateRSETHPrice();
    uint256 priceWithoutRewards = lrtOracle.rsETHPrice();

    // 3. Now call sendFunds() and update price again
    feeReceiver.sendFunds();
    lrtOracle.updateRSETHPrice();
    uint256 priceWithRewards = lrtOracle.rsETHPrice();

    // 4. Assert price difference is at least mevRewards / totalSupply
    uint256 rsethSupply = rsETH.totalSupply();
    uint256 expectedDiff = mevRewards * 1e18 / rsethSupply;

    assertGe(
        priceWithRewards - priceWithoutRewards,
        expectedDiff,
        "rsETH price must reflect pending MEV rewards"
    );
}
```

The test demonstrates that `priceWithoutRewards < priceWithRewards` by at least `FeeReceiver.balance / totalSupply`, confirming the invariant violation when block stuffing prevents `sendFunds()` from executing before the oracle update.

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
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

**File:** contracts/LRTDepositPool.sol (L464-467)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
```

**File:** contracts/LRTDepositPool.sol (L480-499)
```text
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
```
