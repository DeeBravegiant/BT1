### Title
Bridge-Type Switch Permanently Freezes Accumulated rsETH in L1VaultV2 — (`contracts/L1VaultV2.sol`)

---

### Summary

`L1VaultV2` supports two mutually exclusive bridge paths (LayerZero and CCIP). Each bridge function hard-gates on the active `bridgeType` and reverts with `InactiveBridgeType` if the other type is set. Because `setBridgeType()` can be called at any time by `TIMELOCK_ROLE` — including while rsETH already sits in the vault — and because no asset-recovery function exists, any rsETH accumulated before a bridge-type switch becomes permanently undeliverable to L2 users.

---

### Finding Description

`depositETHForL1VaultETH()` (and `depositAssetForL1Vault()`) mint rsETH directly into the vault: [1](#0-0) 

The two bridge functions each enforce an exclusive guard:

`bridgeRsETHToL2` (LayerZero path): [2](#0-1) 

`bridgeRsETHToL2UsingCCIP` (CCIP path): [3](#0-2) 

`setBridgeType()` is callable by `TIMELOCK_ROLE` at any time with no precondition on vault balance: [4](#0-3) 

After the switch, the previously-active bridge function reverts (`InactiveBridgeType`) and the newly-active bridge function also reverts (`InactiveBridgeType`) for the same balance — because the balance was accumulated under the old type. The contract has no `sweep`, `recover`, `rescue`, or any other function that can move rsETH out of the vault by any other path. The `receive()` fallback only accepts ETH: [5](#0-4) 

---

### Impact Explanation

rsETH sitting in the vault at the time of a `setBridgeType()` call is permanently frozen. It cannot be bridged via either path, and there is no on-chain recovery mechanism. This constitutes **permanent freezing of unclaimed yield** — rsETH that was minted from L2 users' bridged ETH and was meant to be delivered to them on L2.

**Impact: Medium — Permanent freezing of unclaimed yield.**

---

### Likelihood Explanation

The scenario does not require any malicious actor. It is a natural operational event:

- The protocol explicitly designed `setBridgeType()` for migration between LayerZero and CCIP.
- `depositETHForL1VaultETH()` is called routinely by `MANAGER_ROLE` to convert incoming ETH into rsETH before bridging.
- A bridge migration (e.g., moving from LayerZero to CCIP) will almost certainly occur while some rsETH is already in the vault, since the deposit and bridge steps are not atomic.
- No coordination mechanism (e.g., "drain vault before switching") is enforced on-chain.

**Likelihood: Medium** — routine operational sequence, no compromise required.

---

### Recommendation

Add a vault-balance precondition to `setBridgeType()` that prevents the switch while rsETH remains in the vault:

```solidity
function setBridgeType(BridgeType _bridgeType) external onlyRole(TIMELOCK_ROLE) {
    if (rsETH.balanceOf(address(this)) > 0) revert VaultNotEmpty();
    ...
}
```

Alternatively, add an emergency sweep function restricted to `DEFAULT_ADMIN_ROLE` or `TIMELOCK_ROLE` that can transfer rsETH to a designated address, enabling manual recovery after an inadvertent switch.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork-test outline (Foundry)
function test_rsETH_frozen_on_bridgeType_switch() public {
    // 1. MANAGER_ROLE deposits ETH → rsETH minted into vault
    vm.prank(manager);
    l1VaultV2.depositETHForL1VaultETH{value: 10 ether}();

    uint256 vaultBalance = rsETH.balanceOf(address(l1VaultV2));
    assertGt(vaultBalance, 0);

    // 2. TIMELOCK_ROLE switches bridge type (LayerZero → CCIP)
    vm.prank(timelockRole);
    l1VaultV2.setBridgeType(L1VaultV2.BridgeType.CCIP);

    // 3. LayerZero bridge now reverts
    vm.prank(manager);
    vm.expectRevert(L1VaultV2.InactiveBridgeType.selector);
    l1VaultV2.bridgeRsETHToL2{value: lzFee}(vaultBalance, vaultBalance - 1, lzFee);

    // 4. Switch back to LayerZero, CCIP bridge reverts
    vm.prank(timelockRole);
    l1VaultV2.setBridgeType(L1VaultV2.BridgeType.LayerZero);

    vm.prank(manager);
    vm.expectRevert(L1VaultV2.InactiveBridgeType.selector);
    l1VaultV2.bridgeRsETHToL2UsingCCIP{value: ccipFee}(vaultBalance);

    // 5. rsETH is permanently stuck — no recovery path
    assertEq(rsETH.balanceOf(address(l1VaultV2)), vaultBalance);
}
```

### Citations

**File:** contracts/L1VaultV2.sol (L224-234)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
```

**File:** contracts/L1VaultV2.sol (L302-304)
```text
        if (bridgeType != BridgeType.LayerZero) {
            revert InactiveBridgeType();
        }
```

**File:** contracts/L1VaultV2.sol (L342-344)
```text
        if (bridgeType != BridgeType.CCIP) {
            revert InactiveBridgeType();
        }
```

**File:** contracts/L1VaultV2.sol (L542-548)
```text
    function setBridgeType(BridgeType _bridgeType) external onlyRole(TIMELOCK_ROLE) {
        if (_bridgeType != BridgeType.LayerZero && _bridgeType != BridgeType.CCIP) {
            revert InvalidBridgeType();
        }
        bridgeType = _bridgeType;
        emit BridgeTypeSet(_bridgeType);
    }
```

**File:** contracts/L1VaultV2.sol (L562-564)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
}
```
