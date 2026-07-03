Audit Report

## Title
Dust Permanently Locked in `TACWETHBridge` Due to OFT Shared-Decimal Rounding With No Recovery Path — (`contracts/bridges/TACWETHBridge.sol`)

## Summary
`bridgeTokenToL1` transfers the full user-supplied `amount` into the bridge contract, but the LayerZero OFT's `send()` internally rounds `amountLD` down to shared-decimal precision and only burns `amountSentLD ≤ amount` from the bridge. The difference (`amount − amountSentLD`) is permanently stranded because `TACWETHBridge` contains no token-recovery function of any kind.

## Finding Description
The flow in `bridgeTokenToL1`:

1. Line 116 pulls the full `amount` from the caller into the bridge: [1](#0-0) 

2. Line 131 calls `wethOFT.send()` with `amountLD: amount`. The LayerZero OFT standard's `_debit` internally applies `_removeDust`, computing `amountSentLD = (amount / decimalConversionRate) * decimalConversionRate`, and burns only `amountSentLD` from the bridge's balance: [2](#0-1) 

3. The `OFTReceipt` struct confirms `amountSentLD` is the amount *actually* debited, which can be strictly less than `amountLD`: [3](#0-2) 

4. `TACWETHBridge` inherits only `AccessControl` and `ReentrancyGuard`. Its sole admin function is `setSlippageTolerance`. There is no `recoverTokens`, `sweep`, or any other path to retrieve stranded ERC-20 tokens: [4](#0-3) [5](#0-4) 

By contrast, `SonicChainNativeTokenBridge` — a parallel bridge in the same repo — explicitly includes a `recoverTokens` admin function as a backstop: [6](#0-5) 

For WETH (18 decimals) with LayerZero's standard 6 shared decimals (`decimalConversionRate = 10^12`), dust per call is up to `10^12 − 1` wei (~0.000001 ETH). This accumulates across every user call with no on-chain recovery path.

## Impact Explanation
Every call to `bridgeTokenToL1` where `amount % decimalConversionRate != 0` permanently locks dust in the bridge. There is no on-chain mechanism to recover these tokens. This constitutes **permanent freezing of user funds**, matching the Critical impact class in the allowed scope.

## Likelihood Explanation
Triggered on every call where the user-supplied `amount` is not aligned to the OFT's shared-decimal precision — the common case for arbitrary user inputs. No attacker involvement, no special preconditions, and no victim mistake is required; normal usage causes the loss on every such transaction.

## Recommendation
1. Before calling `wethOFT.send()`, pre-remove dust from `amount` (e.g., using the OFT's `removeDust` helper or equivalent arithmetic) so that `safeTransferFrom` pulls only the rounded amount.
2. Alternatively, after `send()` returns, refund `amount − oftReceipt.amountSentLD` back to `msg.sender`.
3. Add a `recoverTokens` admin function (as present in `SonicChainNativeTokenBridge`) as a backstop for any residual dust.

## Proof of Concept
```solidity
// MockOFT: simulates OFT shared-decimal rounding
function send(SendParam calldata _sendParam, MessagingFee calldata, address)
    external payable returns (MessagingReceipt memory, OFTReceipt memory)
{
    uint256 amountSentLD = (_sendParam.amountLD / 1e12) * 1e12;
    _burn(msg.sender, amountSentLD); // burns less than what bridge holds
    return (msgReceipt, OFTReceipt(amountSentLD, amountSentLD));
}

// Test
uint256 amount = 1_000_000_000_001; // 1 wei of dust
wethOFT.approve(address(bridge), amount);
bridge.bridgeTokenToL1{value: nativeFee}(recipient, amount);

// Bridge holds 1 wei permanently — no function exists to recover it
assertEq(IERC20(address(wethOFT)).balanceOf(address(bridge)), 1);
```

### Citations

**File:** contracts/bridges/TACWETHBridge.sol (L16-17)
```text
contract TACWETHBridge is IL2TokenBridge, AccessControl, ReentrancyGuard {
    using SafeERC20 for IERC20;
```

**File:** contracts/bridges/TACWETHBridge.sol (L86-93)
```text
    function setSlippageTolerance(uint256 newSlippageTolerance) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newSlippageTolerance > BASIS_POINTS_DIVISOR) {
            revert InvalidSlippageTolerance();
        }

        slippageTolerance = newSlippageTolerance;
        emit SlippageToleranceUpdated(newSlippageTolerance);
    }
```

**File:** contracts/bridges/TACWETHBridge.sol (L116-116)
```text
        IERC20(address(wethOFT)).safeTransferFrom(msg.sender, address(this), amount);
```

**File:** contracts/bridges/TACWETHBridge.sol (L131-133)
```text
        (, OFTReceipt memory oftReceipt) = wethOFT.send{ value: nativeFee }(sendParam, fee, msg.sender);

        emit BridgedWETHToL1(dstLzChainId, recipient, oftReceipt.amountSentLD, oftReceipt.amountReceivedLD);
```

**File:** contracts/external/layerzero/interfaces/IOFT.sol (L37-40)
```text
struct OFTReceipt {
    uint256 amountSentLD; // Amount of tokens ACTUALLY debited from the sender in local decimals
    uint256 amountReceivedLD; // Amount of tokens to be received on the remote side
}
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L156-173)
```text
    /// @notice Allows the admin to recover any tokens sent to this contract by mistake
    /// @param tokenAddress The address of the token to recover
    /// @param recipient The recipient of the recovered tokens
    /// @param amount The amount to recover
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
