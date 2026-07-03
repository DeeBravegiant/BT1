Audit Report

## Title
Missing `isEthDepositEnabled` Guard in `RSETHPoolV3ExternalBridge.deposit()` Bypasses Protocol-Wide ETH Deposit Disable — (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

## Summary
`RSETHPoolV3ExternalBridge.sol` omits the `isEthDepositEnabled` state variable and its corresponding guard that are present in both sibling contracts `RSETHPoolV3.sol` and `RSETHPoolV3WithNativeChainBridge.sol`. When the protocol operator disables ETH deposits across the other two V3-family pools, any unprivileged depositor can continue minting wrsETH through `RSETHPoolV3ExternalBridge.deposit()` without restriction. The protocol's administrative control surface for halting ETH deposits is structurally incomplete.

## Finding Description
`RSETHPoolV3.sol` declares `bool public isEthDepositEnabled` at line 39 and enforces it at the top of `deposit(string)` at line 253:
```solidity
if (!isEthDepositEnabled) revert EthDepositDisabled();
```
`RSETHPoolV3WithNativeChainBridge.sol` mirrors this exactly: `bool public isEthDepositEnabled` at line 45 and the same guard at line 289.

`RSETHPoolV3ExternalBridge.sol` contains no `isEthDepositEnabled` state variable anywhere in its storage layout (lines 42–103) and its `deposit(string memory referralId)` function (lines 366–384) applies only `nonReentrant`, `whenNotPaused`, and `limitDailyMint` — no ETH deposit enable check:

```solidity
function deposit(string memory referralId)
    external
    payable
    nonReentrant
    whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    ...
    wrsETH.mint(msg.sender, rsETHAmount);
```

The existing guards (`whenNotPaused`, `limitDailyMint`) are insufficient substitutes: `whenNotPaused` is a coarser control that also blocks token deposits and bridging operations, and `limitDailyMint` only caps volume, not the deposit-enabled state. Neither replicates the semantics of `isEthDepositEnabled`.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The protocol explicitly provides `setIsEthDepositEnabled` (gated by `TIMELOCK_ROLE`) in both `RSETHPoolV3` and `RSETHPoolV3WithNativeChainBridge` as a first-class operational control to halt ETH minting independently of a full pause. When this flag is set to `false` on those pools (e.g., during an oracle anomaly or planned upgrade), the protocol's intent is that ETH deposits are halted across all pools. `RSETHPoolV3ExternalBridge` does not honor this intent: deposits succeed and `wrsETH.mint` executes at whatever rate the oracle currently reports. The protocol fails to deliver its promised administrative guarantee without any loss of depositor funds per se.

## Likelihood Explanation
The precondition — an operator calling `setIsEthDepositEnabled(false)` on the other pools — is a routine operational action the protocol was explicitly designed to support. No special attacker privilege is required; any depositor who calls `RSETHPoolV3ExternalBridge.deposit{value: X}("")` while the other pools are disabled will succeed. The discrepancy is discoverable by anyone comparing the three pool contracts or by simply attempting a deposit while other pools are disabled.

## Recommendation
Add `bool public isEthDepositEnabled` to `RSETHPoolV3ExternalBridge`'s storage layout and insert the guard at the top of `deposit(string memory referralId)`, mirroring `RSETHPoolV3`:
```solidity
if (!isEthDepositEnabled) revert EthDepositDisabled();
```
Add a `setIsEthDepositEnabled(bool)` setter gated by `TIMELOCK_ROLE`, consistent with the sibling contracts. Ensure the `EthDepositDisabled` error is declared in the contract's error list.

## Proof of Concept
1. Operator calls `RSETHPoolV3.setIsEthDepositEnabled(false)` and `RSETHPoolV3WithNativeChainBridge.setIsEthDepositEnabled(false)` — both pools now revert on `deposit()` with `EthDepositDisabled`.
2. Depositor calls `RSETHPoolV3ExternalBridge.deposit{value: 10 ether}("")`.
3. The call passes `whenNotPaused` (contract is not paused) and `limitDailyMint` (within daily limit).
4. No `isEthDepositEnabled` check exists; execution reaches `wrsETH.mint(msg.sender, rsETHAmount)` and succeeds.
5. Depositor receives wrsETH at the current oracle rate while the protocol believed ETH deposits were disabled.

**Foundry fork test sketch:**
```solidity
function test_depositBypassesEthDepositDisable() public {
    // Operator disables ETH deposits on sibling pools
    vm.prank(timelockRole);
    rsethPoolV3.setIsEthDepositEnabled(false);
    vm.prank(timelockRole);
    rsethPoolV3NativeBridge.setIsEthDepositEnabled(false);

    // Sibling pools revert
    vm.expectRevert(RSETHPoolV3.EthDepositDisabled.selector);
    rsethPoolV3.deposit{value: 1 ether}("");

    // ExternalBridge pool succeeds — no guard
    uint256 balanceBefore = wrsETH.balanceOf(attacker);
    vm.prank(attacker);
    rsethPoolV3ExternalBridge.deposit{value: 1 ether}("");
    assertGt(wrsETH.balanceOf(attacker), balanceBefore);
}
```