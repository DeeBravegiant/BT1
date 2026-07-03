### Title
Permissionless `initialize2()` Can Lock Wrong `ethxAddress`, Permanently Freezing Yield Accounting — (`contracts/oracles/EthXPriceOracle.sol`)

---

### Summary

`EthXPriceOracle.initialize2()` carries no access-control modifier. Any caller can invoke it during the window between `initialize()` and the legitimate upgrade transaction. If Stader's config is mid-rotation at that moment, `ethxAddress` is permanently set to a stale/wrong token, causing every subsequent `updateRSETHPrice()` call to revert.

---

### Finding Description

`initialize2()` is declared `external reinitializer(2)` with no role guard: [1](#0-0) 

`reinitializer(2)` only enforces single-execution; it does not restrict the caller. Compare with `LRTOracle.reinitialize`, which correctly gates the same pattern behind `onlyLRTManager`: [2](#0-1) 

Once `initialize2()` is called, `ethxAddress` is fixed to whatever `IStaderConfig.getETHxToken()` returned at that instant: [3](#0-2) 

`getAssetPrice` then hard-reverts for any address that does not match: [4](#0-3) 

`LRTOracle._getTotalEthInProtocol()` iterates every supported asset and calls `getAssetPrice` for each one: [5](#0-4) 

A revert inside that loop propagates through `_updateRsETHPrice()` → `updateRSETHPrice()`, permanently halting price updates and fee minting. [6](#0-5) 

---

### Impact Explanation

**Scope: Medium — Permanent freezing of unclaimed yield** (not "High — Theft of unclaimed yield" as claimed in the question; the yield is frozen, not redirected).

- `rsETHPrice` is never updated.
- Protocol fee rsETH is never minted to treasury.
- No recovery path exists: `reinitializer(2)` is consumed, so the correct address cannot be written again via `initialize2()`. Fixing it requires a new `initialize3()` upgrade.

---

### Likelihood Explanation

**Low.** Two simultaneous preconditions are required:

1. The proxy is at initializer version 1 (a bounded deployment window).
2. Stader's `IStaderConfig.getETHxToken()` returns a wrong/stale address at the moment of the call (an external-dependency condition that is not attacker-controlled).

If Stader's config returns the correct address, the attacker merely consumes the slot harmlessly (the address is set correctly). The harmful scenario requires the external protocol to be in a transient mid-rotation state, which is not guaranteed and cannot be forced by the attacker alone.

---

### Recommendation

Add an access-control modifier to `initialize2()`, consistent with how `LRTOracle.reinitialize` is protected:

```solidity
function initialize2() external reinitializer(2) onlyLRTAdmin {
    ...
}
```

`EthXPriceOracle` does not currently inherit `LRTConfigRoleChecker`, so the simplest fix is to either add that inheritance or introduce an `owner`-style guard set during `initialize()`.

---

### Proof of Concept

```solidity
// Fork test (local/private testnet only)
function test_initialize2_griefing() public {
    // Deploy proxy at version 1
    EthXPriceOracle oracle = EthXPriceOracle(proxy);
    // oracle.initialize(stakePoolsManager) already called

    // Mock StaderConfig to return wrong address during "mid-rotation"
    vm.mockCall(
        staderConfig,
        abi.encodeWithSelector(IStaderConfig.getETHxToken.selector),
        abi.encode(address(0xdead))
    );

    // Attacker calls initialize2() — no role check, succeeds
    vm.prank(attacker);
    oracle.initialize2();

    assertEq(oracle.ethxAddress(), address(0xdead));

    // Subsequent updateRSETHPrice() reverts because realEthx != 0xdead
    vm.expectRevert(EthXPriceOracle.InvalidAsset.selector);
    lrtOracle.updateRSETHPrice();

    // Treasury rsETH balance never increases
    assertEq(rsETH.balanceOf(treasury), 0);
}
```

### Citations

**File:** contracts/oracles/EthXPriceOracle.sol (L38-41)
```text
    function initialize2() external reinitializer(2) {
        address staderConfigProxyAddress = IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).staderConfig();
        ethxAddress = IStaderConfig(staderConfigProxyAddress).getETHxToken();
    }
```

**File:** contracts/oracles/EthXPriceOracle.sol (L46-49)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != ethxAddress) {
            revert InvalidAsset();
        }
```

**File:** contracts/LRTOracle.sol (L72-72)
```text
    function reinitialize(uint256 _feePeriodStartTime) external reinitializer(2) onlyLRTManager {
```

**File:** contracts/LRTOracle.sol (L231-231)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();
```

**File:** contracts/LRTOracle.sol (L336-340)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
```
