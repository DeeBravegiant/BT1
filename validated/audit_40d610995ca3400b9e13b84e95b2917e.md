### Title
Force-sent ETH via `selfdestruct` Inflates `address(this).balance`-Based ETH Accounting, Manipulating rsETH Price and Blocking Deposits - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.getETHDistributionData()` computes the protocol's ETH holdings using raw `address(this).balance`, `nodeDelegatorQueue[i].balance`, and `lrtUnstakingVault.balance`. Because Solidity contracts cannot reject ETH sent via `selfdestruct`, an attacker can permanently inflate any of these balances without going through the normal deposit path. This inflated balance propagates into the rsETH price calculation and the deposit-limit guard, causing new depositors to receive fewer rsETH tokens than they are entitled to and, if the deposit cap is breached, blocking all further ETH deposits until an admin intervenes.

---

### Finding Description

`LRTDepositPool.getETHDistributionData()` is the canonical source of ETH accounting for the entire protocol:

```solidity
// contracts/LRTDepositPool.sol  lines 480, 485, 496
ethLyingInDepositPool = address(this).balance;          // line 480
ethLyingInNDCs += nodeDelegatorQueue[i].balance;        // line 485
ethLyingInUnstakingVault = lrtUnstakingVault.balance;   // line 496
``` [1](#0-0) 

This function is called by `getTotalAssetDeposits(ETH_TOKEN)`, which is consumed in two critical paths:

**Path 1 — rsETH price (oracle)**

`LRTOracle._getTotalEthInProtocol()` calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for every supported asset, including ETH:

```solidity
// contracts/LRTOracle.sol  lines 341-343
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [2](#0-1) 

`_updateRsETHPrice()` then divides this inflated total by `rsethSupply` to produce the new rsETH price:

```solidity
// contracts/LRTOracle.sol  line 250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [3](#0-2) 

**Path 2 — deposit-limit guard**

`_checkIfDepositAmountExceedesCurrentLimit` uses the same `getTotalAssetDeposits` result:

```solidity
// contracts/LRTDepositPool.sol  lines 677-680
uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
``` [4](#0-3) 

If `totalAssetDeposits` exceeds the configured cap, `_beforeDeposit` reverts with `MaximumDepositLimitReached`, blocking all ETH deposits:

```solidity
// contracts/LRTDepositPool.sol  lines 661-663
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [5](#0-4) 

Because `address(this).balance` (and `.balance` on any address) includes ETH force-sent via `selfdestruct`, an attacker can inflate these values without minting any rsETH, without going through `depositETH`, and without triggering any access-control check.

---

### Impact Explanation

**Impact 1 — rsETH price inflation (Low: contract fails to deliver promised returns)**

After the force-send, the next call to `updateRSETHPrice()` (callable by anyone) computes a higher `newRsETHPrice`. Every subsequent depositor receives:

```
rsethAmountToMint = (amount × assetPrice) / rsETHPrice
``` [6](#0-5) 

With an artificially elevated `rsETHPrice`, `rsethAmountToMint` is smaller than it should be. Depositors receive fewer rsETH tokens than the protocol's fair-value formula promises, with no recourse.

**Impact 2 — Deposit-limit DoS (Medium: temporary freezing of funds)**

If the force-sent amount is large enough to push `totalAssetDeposits(ETH_TOKEN)` above `depositLimitByAsset(ETH_TOKEN)`, every call to `depositETH` reverts. ETH deposits are frozen until an admin raises the deposit cap. Because the ETH is permanently embedded in the contract balance (selfdestruct cannot be reversed), the admin must act to restore service.

---

### Likelihood Explanation

The attack requires the attacker to sacrifice ETH with no direct financial gain (pure self-inflicted loss). However:

- No special permissions are required; any externally owned account can deploy a self-destructing contract.
- The cost to push a protocol with a modest deposit cap over its limit may be low relative to the disruption caused.
- A griefing actor (e.g., a competitor or a protocol adversary) has a plausible motive to block deposits temporarily.

Likelihood is **low** but non-zero.

---

### Recommendation

Replace raw `address(this).balance` and `.balance` reads with an internal accounting variable that is incremented only through the protocol's controlled receive paths (`receiveFromNodeDelegator`, `receiveFromLRTConverter`, `depositETH`, etc.) and decremented on outbound transfers. This mirrors the fix suggested in the referenced GenesisGroup report: track deposits through an internal counter rather than relying on the contract's native balance.

```solidity
// Example pattern
uint256 internal _trackedETHBalance;

function receiveFromNodeDelegator() external payable {
    _trackedETHBalance += msg.value;
}

// In getETHDistributionData():
ethLyingInDepositPool = _trackedETHBalance; // instead of address(this).balance
```

Apply the same pattern to `NodeDelegator` and `LRTUnstakingVault` balances used in `getETHDistributionData()`.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract ForceETH {
    constructor(address target) payable {
        selfdestruct(payable(target));
    }
}

// Attack steps:
// 1. Deploy ForceETH with, e.g., depositLimitByAsset(ETH) + 1 wei, targeting LRTDepositPool.
//    new ForceETH{value: depositLimit + 1}(address(lrtDepositPool));
//
// 2. address(lrtDepositPool).balance is now > depositLimitByAsset(ETH).
//
// 3. Any user calling depositETH() now hits:
//    _checkIfDepositAmountExceedesCurrentLimit → totalAssetDeposits > depositLimit → revert MaximumDepositLimitReached
//
// 4. Separately, the next call to updateRSETHPrice() (permissionless) computes:
//    totalETHInProtocol += inflated ethLyingInDepositPool
//    newRsETHPrice = inflated total / rsethSupply  → higher price
//    All subsequent depositors receive fewer rsETH tokens.
```

### Citations

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

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
```

**File:** contracts/LRTDepositPool.sol (L677-680)
```text
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
