Audit Report

## Title
Missing ETH Recovery Mechanism Causes Bridged ETH to Be Frozen When Deposit Cap Is Reached - (File: contracts/L1Vault.sol, contracts/L1VaultV2.sol)

## Summary
`L1Vault` and `L1VaultV2` accept ETH from L2 bridges via a bare `receive()` function but expose no alternative ETH egress path. The sole function that moves this ETH onward, `depositETHForL1VaultETH()`, unconditionally calls `lrtDepositPool.depositETH`, which reverts with `MaximumDepositLimitReached` when the protocol-wide ETH deposit cap is reached through ordinary user activity. Because neither contract contains any other ETH withdrawal mechanism, all ETH sitting in the vault is frozen for the duration of that condition.

## Finding Description
Both contracts accept ETH passively:

`L1Vault.sol` line 368 / `L1VaultV2.sol` line 563:
```solidity
receive() external payable { }
```

The only function that can move that ETH out is `depositETHForL1VaultETH()`:

`L1Vault.sol` lines 150–161 / `L1VaultV2.sol` lines 224–235:
```solidity
function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
    uint256 balanceOfETH = address(this).balance;
    uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);
    if (rsETHAmountToMint == 0) { revert InvalidMinRSETHAmountExpected(); }
    lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");
    ...
}
```

`LRTDepositPool.depositETH` calls `_beforeDeposit`, which enforces the deposit cap:

`LRTDepositPool.sol` lines 648–663:
```solidity
function _beforeDeposit(...) private view returns (uint256 rsethAmountToMint) {
    ...
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
        revert MaximumDepositLimitReached();
    }
    ...
}
```

The cap check at lines 676–682:
```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    ...
}
```

`totalAssetDeposits` aggregates ETH across the deposit pool, all node delegators, EigenLayer, the converter, and the unstaking vault. Any ordinary L1 depositor can push this total past the configured limit. Once the limit is exceeded, every call to `depositETHForL1VaultETH()` reverts, and there is no other function in either `L1Vault` or `L1VaultV2` that can move the accumulated ETH out. A full-text search of both contracts confirms the absence of any `withdrawETH`, `rescueETH`, or equivalent function.

## Impact Explanation
ETH bridged from L2 pools accumulates in `L1Vault`/`L1VaultV2`. When the protocol ETH deposit cap is reached — a condition reachable by unprivileged depositors on L1 — the manager cannot call `depositETHForL1VaultETH()` and there is no alternative path to move the ETH. The funds are frozen for the entire duration of the cap condition. This matches the allowed impact: **Medium — Temporary freezing of funds**.

## Likelihood Explanation
The ETH deposit cap (`lrtConfig.depositLimitByAsset(ETH_TOKEN)`) is a finite protocol parameter. Ordinary L1 depositors calling `LRTDepositPool.depositETH` can collectively push `getTotalAssetDeposits(ETH_TOKEN)` past that limit without any privileged action. The L2 bridge continues forwarding ETH to the vault regardless of the cap state, so the frozen amount grows over time. No privileged role, collusion, or oracle compromise is required to trigger the freeze.

## Recommendation
Add an access-restricted ETH recovery function to both `L1Vault` and `L1VaultV2`:

```solidity
function withdrawETH(address payable recipient, uint256 amount)
    external
    nonReentrant
    onlyRole(TIMELOCK_ROLE)
{
    UtilLib.checkNonZeroAddress(recipient);
    (bool success,) = recipient.call{ value: amount }("");
    if (!success) revert EthTransferFailed();
}
```

Gating it behind `TIMELOCK_ROLE` ensures the recovery path cannot be abused while still allowing the protocol to unblock frozen ETH without a contract upgrade.

## Proof of Concept
1. Deploy a fork of mainnet with `L1Vault` and `LRTDepositPool` at their current addresses.
2. Simulate L2 bridge activity: call `L1Vault.receive()` (or send ETH directly) to accumulate ETH in the vault.
3. Using multiple unprivileged accounts, call `LRTDepositPool.depositETH` until `getTotalAssetDeposits(ETH_TOKEN) > lrtConfig.depositLimitByAsset(ETH_TOKEN)`.
4. Attempt `L1Vault.depositETHForL1VaultETH()` as the manager — the call reverts with `MaximumDepositLimitReached`.
5. Confirm no other function in `L1Vault` can move the ETH out; `address(L1Vault).balance` remains unchanged.
6. Repeat steps 2–5 for `L1VaultV2`.