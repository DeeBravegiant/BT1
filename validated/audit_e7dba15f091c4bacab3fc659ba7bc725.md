### Title
Unprotected Raw ETH Balance Reads in `getETHDistributionData()` Allow Forced TVL Inflation, Blocking Public Price Updates and Freezing Protocol Fee Yield — (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`getETHDistributionData()` in `LRTDepositPool.sol` computes the protocol's ETH TVL by reading raw `.balance` values from the DepositPool, each NodeDelegator, and the UnstakingVault. All three contract types expose an unrestricted `receive() external payable` function. Any unprivileged actor can send ETH directly to these contracts, inflating the apparent TVL without minting any rsETH. This inflated TVL propagates into `_updateRsETHPrice()` via `_getTotalEthInProtocol()`. If the resulting price increase exceeds `pricePercentageLimit`, the public `updateRSETHPrice()` reverts for all non-manager callers, permanently blocking permissionless price updates and freezing protocol fee yield until a manager intervenes — an intervention the attacker can continuously defeat by re-inflating the balance.

---

### Finding Description

`getETHDistributionData()` aggregates the protocol's ETH position across three locations using raw balance reads:

```
ethLyingInDepositPool = address(this).balance;          // line 480
ethLyingInNDCs       += nodeDelegatorQueue[i].balance;  // line 485
ethLyingInUnstakingVault = lrtUnstakingVault.balance;   // line 496
``` [1](#0-0) 

All three target contracts expose an open `receive()`:

- `LRTDepositPool`: `receive() external payable { }` [2](#0-1) 
- `NodeDelegator`: `receive() external payable { emit ETHReceived(msg.sender, msg.value); }` [3](#0-2) 
- `LRTUnstakingVault`: also has `receive() external payable` [4](#0-3) 

This raw balance is consumed by `getTotalAssetDeposits(ETH_TOKEN)` → `_getTotalEthInProtocol()` → `_updateRsETHPrice()`:

```solidity
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [5](#0-4) 

Inside `_updateRsETHPrice()`, the new price is computed as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [6](#0-5) 

If `newRsETHPrice` exceeds `highestRsethPrice` by more than `pricePercentageLimit`, the public `updateRSETHPrice()` reverts for any non-manager caller:

```solidity
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        revert PriceAboveDailyThreshold();
    }
}
``` [7](#0-6) 

The public entry point is:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [8](#0-7) 

---

### Impact Explanation

**Primary impact — Permanent freezing of unclaimed yield (Medium):**

Protocol fee rsETH is minted inside `_updateRsETHPrice()`:

```solidity
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
``` [9](#0-8) 

When `updateRSETHPrice()` is blocked for non-managers, the treasury cannot receive its periodic fee rsETH. An attacker who continuously sends small ETH amounts to the DepositPool or any NDC can sustain this DoS indefinitely, because each new send re-inflates the balance above the threshold before the manager can act.

**Secondary impact — oracle/rate abuse:**

If the inflation is kept within `pricePercentageLimit` (i.e., the attacker sends a smaller amount), `updateRSETHPrice()` succeeds and records an inflated `rsETHPrice`. This inflated price is then used in `getExpectedAssetAmount()` in `LRTWithdrawalManager`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [10](#0-9) 

Existing rsETH holders who withdraw immediately after the inflation receive more underlying assets than they are entitled to, at the expense of remaining depositors.

---

### Likelihood Explanation

**Medium.** The attack requires only the ability to send ETH to a public `receive()` function — no special role, no flash loan, no complex setup. The attacker loses the ETH sent (it is donated to the protocol TVL), making sustained DoS costly but not impossible for a well-funded griever. A single send of sufficient size (enough to push the price above `pricePercentageLimit`) is enough to block the public price update for the entire block window. The attacker can repeat this every time the manager attempts to restore normal operation.

---

### Recommendation

1. **Track deposited ETH explicitly** rather than relying on `address(this).balance`. Maintain an internal accounting variable (e.g., `totalTrackedETH`) that is incremented only through controlled deposit paths (`depositETH`, `receiveFromNodeDelegator`, `receiveFromRewardReceiver`, etc.) and decremented on withdrawals. Use this variable instead of `address(this).balance` in `getETHDistributionData()`.

2. Apply the same pattern to NodeDelegator and UnstakingVault: replace `.balance` reads with internal accounting that is updated only through authorized ETH-movement functions.

3. Any ETH received via the raw `receive()` fallback that is not tracked should either be rejected (revert in `receive()`) or quarantined in a separate variable and only incorporated into TVL after manager approval.

---

### Proof of Concept

```
1. Protocol state: rsETH supply = 1000 rsETH, rsETHPrice = 1.05 ETH,
   highestRsethPrice = 1.05 ETH, pricePercentageLimit = 1% (1e16).

2. Attacker calls:
       (new AttackContract{value: 50 ether}).attack(depositPoolAddress);
   where attack() does:
       payable(depositPoolAddress).call{value: 50 ether}("");

3. LRTDepositPool.receive() accepts the 50 ETH.
   address(depositPool).balance increases by 50 ETH.

4. getETHDistributionData() now returns ethLyingInDepositPool += 50 ETH.
   getTotalAssetDeposits(ETH_TOKEN) is inflated by 50 ETH.
   _getTotalEthInProtocol() returns inflated totalETHInProtocol.

5. newRsETHPrice = (inflated TVL) / 1000 rsETH >> 1.05 * 1.01 ETH.
   isPriceIncreaseOffLimit = true.

6. Any bot or user calling updateRSETHPrice() receives:
       revert PriceAboveDailyThreshold();

7. Protocol fee rsETH minting is blocked. rsETHPrice remains stale.
   Attacker repeats step 2 whenever the manager attempts to restore
   the price, sustaining the DoS indefinitely.
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-496)
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
```

**File:** contracts/NodeDelegator.sol (L81-83)
```text
    receive() external payable {
        emit ETHReceived(msg.sender, msg.value);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L77-80)
```text
    /*//////////////////////////////////////////////////////////////
                        receive functions
    //////////////////////////////////////////////////////////////*/

```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
