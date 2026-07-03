The core mechanics are confirmed. Here is my analysis:

**Key facts from the code:**

1. `TACWETHBridge` does NOT inherit `Recoverable` and has no `recoverTokens` or sweep function — unlike `SonicChainNativeTokenBridge` which explicitly includes one. [1](#0-0) 

2. The flow in `bridgeTokenToL1`:
   - Line 116 pulls the full `amount` from the user into the bridge. [2](#0-1) 
   - Line 131 calls `wethOFT.send()` with `amountLD: amount`. The LayerZero OFT standard's `_debit` internally rounds `amountLD` down to shared-decimal precision, burning only `amountSentLD ≤ amount` from the bridge. [3](#0-2) 
   - Dust = `amount − amountSentLD` remains in the bridge contract permanently.

3. `OFTReceipt.amountSentLD` is defined as "Amount of tokens ACTUALLY debited from the sender in local decimals," confirming it can differ from `amountLD`. [4](#0-3) 

4. The entire `TACWETHBridge` contract has only one admin function (`setSlippageTolerance`) — no token recovery path exists. [5](#0-4) 

**Assessment of the claimed "direct theft / double-accounting exploit":**

The "double-accounting exploit" framing — where an attacker exploits the event discrepancy to receive more on L1 than was bridged — does **not** hold. The OFT protocol itself transmits `amountSentLD` cross-chain; L1 credits `amountReceivedLD`, not `amount`. No attacker can leverage the event mismatch to extract extra funds on L1. The impact is not "direct theft by an attacker."

**The real, concrete impact:**

The dust (`amount − amountSentLD`) is permanently locked in `TACWETHBridge` with no recovery mechanism. For WETH (18 decimals) with LayerZero's standard 6 shared decimals, dust per call is up to `10^12 − 1` wei (~0.000001 ETH). This accumulates across every user call. This constitutes **permanent freezing of user funds**, which is in scope as Critical.

---

### Title
Dust Permanently Locked in TACWETHBridge Due to OFT Shared-Decimal Rounding With No Recovery Path — (`contracts/bridges/TACWETHBridge.sol`)

### Summary
`bridgeTokenToL1` pulls the full `amount` from the user but the LayerZero OFT's `send()` only debits `amountSentLD ≤ amount` (rounded to shared-decimal precision). The difference (`amount − amountSentLD`) is permanently stranded in the bridge contract, which has no token-recovery function.

### Finding Description
In `bridgeTokenToL1`:

```solidity
// Line 116 — pulls full `amount` from user
IERC20(address(wethOFT)).safeTransferFrom(msg.sender, address(this), amount);

// Line 131 — OFT burns only amountSentLD (≤ amount) from bridge
(, OFTReceipt memory oftReceipt) = wethOFT.send{ value: nativeFee }(sendParam, fee, msg.sender);

// Line 133 — event logs amountSentLD, not amount
emit BridgedWETHToL1(dstLzChainId, recipient, oftReceipt.amountSentLD, oftReceipt.amountReceivedLD);
```

The LayerZero OFT standard converts `amountLD` → shared decimals (floor division) → back to local decimals to produce `amountSentLD`. For WETH (18 decimals, 6 shared decimals), the conversion is:

```
amountSentLD = (amount / 10^12) * 10^12
dust         = amount % 10^12   // up to 999,999,999,999 wei per call
```

The bridge holds `dust` tokens after every call. `TACWETHBridge` inherits only `AccessControl` and `ReentrancyGuard`; it has no `recoverTokens`, `sweep`, or any other function to retrieve stranded ERC-20 tokens. Compare with `SonicChainNativeTokenBridge`, which explicitly includes a `recoverTokens` admin function. [6](#0-5) [7](#0-6) 

### Impact Explanation
Every call to `bridgeTokenToL1` with a non-dust-aligned `amount` permanently locks up to `~10^12 − 1` wei of WETH in the bridge. There is no on-chain path to recover these tokens. Over many calls the aggregate loss is material. Impact: **Critical — Permanent freezing of funds.**

### Likelihood Explanation
Triggered on every call where `amount % 10^12 != 0`, which is the common case for arbitrary user-supplied amounts. No special preconditions or attacker involvement required — normal usage causes the loss.

### Recommendation
1. Before calling `wethOFT.send()`, pre-remove dust from `amount` using the OFT's `removeDust` (or equivalent) so that `safeTransferFrom` pulls only the rounded amount.
2. Alternatively, after `send()` returns, refund `amount − oftReceipt.amountSentLD` back to `msg.sender`.
3. Add a `recoverTokens` admin function (as present in `SonicChainNativeTokenBridge`) as a backstop.

### Proof of Concept
```solidity
// MockOFT: returns amountSentLD = amount - (amount % 1e12)
function send(...) external payable returns (MessagingReceipt memory, OFTReceipt memory) {
    uint256 amountSentLD = _sendParam.amountLD - (_sendParam.amountLD % 1e12);
    _burn(msg.sender, amountSentLD); // burns less than pulled
    return (msgReceipt, OFTReceipt(amountSentLD, amountSentLD));
}

// Test
uint256 amount = 1_000_000_000_001; // 1 wei of dust
wethOFT.approve(address(bridge), amount);
bridge.bridgeTokenToL1{value: nativeFee}(recipient, amount);

// Assert: bridge holds 1 wei permanently
assertEq(IERC20(address(wethOFT)).balanceOf(address(bridge)), 1);
// Assert: no function exists to recover it
// (static analysis: TACWETHBridge has no recoverTokens/sweep)
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
