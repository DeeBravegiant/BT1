Audit Report

## Title
Missing Recovery Function Causes Permanent Locking of OFT Decimal-Dust WETH in TACWETHBridge — (`contracts/bridges/TACWETHBridge.sol`)

## Summary

`TACWETHBridge.bridgeTokenToL1()` transfers the full user-supplied `amount` of WETH into itself, then calls `wethOFT.send()` which, per the LayerZero OFT standard, burns only `amountSentLD ≤ amount` (after `_removeDust()` truncation to shared-decimal precision). The remainder `amount − amountSentLD` is left as a WETH balance in the bridge. `TACWETHBridge` contains no function capable of moving ERC-20 tokens out of the contract, making this residual permanently irrecoverable.

## Finding Description

In `bridgeTokenToL1`: [1](#0-0) 

The bridge pulls the full `amount` of WETH from the caller into `address(this)`. [2](#0-1) 

`wethOFT.send()` is then called with `amountLD: amount`. Per the LayerZero OFT v2 standard, `send()` internally calls `_debit(msg.sender, amountLD, ...)` which applies `_removeDust()` — truncating the amount to the nearest `10^(localDecimals − sharedDecimals)` unit — and burns only `amountSentLD` from `msg.sender` (the bridge). The `OFTReceipt.amountSentLD` field is defined as: [3](#0-2) 

When `sharedDecimals < 18` (standard for WETH OFTs, typically 6 shared decimals), `amountSentLD < amount` by up to `10^(18 − sharedDecimals)` wei per call. The difference remains as a WETH balance in `TACWETHBridge`.

`TACWETHBridge` is declared as: [4](#0-3) 

It inherits only `AccessControl` and `ReentrancyGuard`. The full contract (192 lines) contains no function that transfers ERC-20 tokens out of the contract. There is no `recoverTokens`, no `emergencyRecover`, no `Recoverable` inheritance, and no upgrade proxy.

By contrast, every sibling bridge contract in the codebase provides an escape hatch. `SonicBridgeReceiver` provides: [5](#0-4) 

`SonicChainNativeTokenBridge` provides a standalone `recoverTokens`: [6](#0-5) 

And the codebase-wide `Recoverable` utility provides: [7](#0-6) 

`TACWETHBridge` has none of these.

## Impact Explanation

Every call to `bridgeTokenToL1` where `amount % 10^(18 − sharedDecimals) != 0` leaves up to `10^(18 − sharedDecimals)` wei of WETH permanently locked in the bridge. For a standard WETH OFT with 6 shared decimals this is up to `10^12` wei (≈ 0.000001 ETH) per transaction. The dust accumulates across all users and all calls with no mechanism for recovery. The contract is not upgradeable and has no self-destruct. This constitutes **Critical — Permanent freezing of funds**.

## Likelihood Explanation

This triggers on every single call to `bridgeTokenToL1` where the user-supplied `amount` is not already aligned to the OFT's shared-decimal granularity, which is the common case for arbitrary user-supplied amounts. No special attacker action is required; normal protocol usage by any unprivileged caller is sufficient to trigger and accumulate the locked dust.

## Recommendation

1. Add `Recoverable` inheritance to `TACWETHBridge` (consistent with the rest of the codebase), or add a standalone `emergencyRecover` admin function matching the pattern in `SonicBridgeReceiver`.
2. Additionally, refund dust to the caller after the `send()` call:

```solidity
uint256 balanceBefore = IERC20(address(wethOFT)).balanceOf(address(this));
IERC20(address(wethOFT)).safeTransferFrom(msg.sender, address(this), amount);
(, OFTReceipt memory oftReceipt) = wethOFT.send{value: nativeFee}(sendParam, fee, msg.sender);
uint256 dust = IERC20(address(wethOFT)).balanceOf(address(this)) - balanceBefore;
if (dust > 0) IERC20(address(wethOFT)).safeTransfer(msg.sender, dust);
```

## Proof of Concept

Foundry fork/unit test plan:

1. Deploy a mock WETH OFT with `sharedDecimals = 6` implementing the standard LayerZero `_removeDust()` logic (truncates to `1e12` granularity).
2. Deploy `TACWETHBridge` pointing to the mock OFT.
3. Call `bridgeTokenToL1(recipient, 1 ether + 500)` — an amount with non-zero dust (`500 < 1e12`).
4. Assert `IERC20(wethOFT).balanceOf(address(bridge)) == 500`.
5. Attempt to call any function on `TACWETHBridge` to recover the 500 wei — confirm no such function exists and the balance is permanently locked.
6. Repeat 1000 times with fuzz inputs; assert cumulative locked balance grows unboundedly with no recovery path.

### Citations

**File:** contracts/bridges/TACWETHBridge.sol (L16-16)
```text
contract TACWETHBridge is IL2TokenBridge, AccessControl, ReentrancyGuard {
```

**File:** contracts/bridges/TACWETHBridge.sol (L116-116)
```text
        IERC20(address(wethOFT)).safeTransferFrom(msg.sender, address(this), amount);
```

**File:** contracts/bridges/TACWETHBridge.sol (L131-131)
```text
        (, OFTReceipt memory oftReceipt) = wethOFT.send{ value: nativeFee }(sendParam, fee, msg.sender);
```

**File:** contracts/external/layerzero/interfaces/IOFT.sol (L37-40)
```text
struct OFTReceipt {
    uint256 amountSentLD; // Amount of tokens ACTUALLY debited from the sender in local decimals
    uint256 amountReceivedLD; // Amount of tokens to be received on the remote side
}
```

**File:** contracts/bridges/SonicBridgeReceiver.sol (L164-172)
```text
    function emergencyRecover(address token, address recipient, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(recipient);

        uint256 balance = IERC20(token).balanceOf(address(this));
        uint256 recoverAmount = amount == 0 ? balance : amount;
        if (recoverAmount > balance) revert InsufficientBalance();

        IERC20(token).safeTransfer(recipient, recoverAmount);
    }
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L160-173)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert InvalidAmount();

        IERC20(tokenAddress).safeTransfer(recipient, amount);
    }
```

**File:** contracts/utils/Recoverable.sol (L41-57)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert ZeroAmount();
        if (IERC20(tokenAddress).balanceOf(address(this)) < amount) revert InsufficientBalance();

        IERC20(tokenAddress).safeTransfer(recipient, amount);

        emit TokensRecovered(tokenAddress, recipient, amount);
    }
```
