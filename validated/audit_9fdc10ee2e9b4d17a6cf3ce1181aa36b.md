### Title
OFT Dust Removal Causes Cumulative rsETH Shortfall in L2 Pool — (`contracts/L1Vault.sol`, `contracts/L1VaultV2.sol`)

---

### Summary

`L1Vault.bridgeRsETHToL2()` and `L1VaultV2.bridgeRsETHToL2()` send rsETH to L2 via the LayerZero OFT adapter. The OFT standard silently removes "dust" (sub-precision amounts) before every cross-chain transfer, so the L2 pool receives `amount - dust` instead of `amount`. The return value of `oftAdapter.send()` is discarded in both contracts, meaning the protocol never observes the actual amount delivered. Over repeated bridging cycles the L2 pool accumulates a growing rsETH deficit, eventually causing user deposit calls to revert when the pool's rsETH balance is exhausted.

---

### Finding Description

`L1Vault.bridgeRsETHToL2()` constructs a `SendParam` with `amountLD = amount` and calls `oftAdapter.send()`, discarding the returned `OFTReceipt`:

```solidity
oftAdapter.send{ value: nativeFee }(sendParam, fee, msg.sender);
// OFTReceipt (amountSentLD, amountReceivedLD) is never captured
``` [1](#0-0) 

The identical pattern exists in `L1VaultV2`: [2](#0-1) 

Per the LayerZero OFT documentation (referenced in the original report), every `send()` call "cleans" the amount by truncating any decimal precision that cannot be represented in the shared 6-decimal system. For an 18-decimal token like rsETH, dust = `amount % 1e12`. For a transfer of `1.234567890123456789 rsETH`, dust = `123456789 wei`. The dust is retained in `L1Vault` (the OFT adapter only locks `amountSentLD = amount - dust`), but the L2 pool receives `amountReceivedLD = amount - dust`.

The L2 pools (`RSETHPoolNoWrapper`, `RSETHPoolV3`, etc.) hold a real rsETH balance and transfer it directly to depositors:

```solidity
rsETH.safeTransfer(msg.sender, rsETHAmount);
``` [3](#0-2) 

Each bridging cycle delivers slightly less rsETH than the manager intended. After N cycles the pool's balance is short by `N × dust`. When the shortfall exceeds the pool's remaining balance, `safeTransfer` reverts and all subsequent user deposits fail.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

User deposits to the L2 pool revert with an ERC-20 insufficient-balance error once the cumulative dust shortfall depletes the pool's rsETH balance. Users cannot receive rsETH until the manager manually bridges additional rsETH to top up the pool. The funds are not permanently lost (dust accumulates in `L1Vault`), but users are temporarily unable to access the service the protocol promises.

---

### Likelihood Explanation

The dust removal occurs on **every** `bridgeRsETHToL2` call — it is an unconditional property of the OFT standard for 18-decimal tokens. No special conditions or attacker action are required. The only variable is how many cycles are needed before the shortfall becomes large enough to block a deposit; for active pools this is a matter of normal operation.

---

### Recommendation

Capture the `OFTReceipt` returned by `oftAdapter.send()` and use `oftReceipt.amountSentLD` (the actual amount debited from `L1Vault`) as the canonical bridged amount. Before calling `send`, pre-compute the dust-cleaned amount using the OFT adapter's `quoteOFT` or equivalent helper so that the `amountLD` passed in is already dust-free. This ensures the L2 pool's expected replenishment exactly matches what it receives.

```solidity
// Example fix: pre-clean the amount
uint256 cleanedAmount = (amount / 1e12) * 1e12; // for 18→6 shared decimals
SendParam memory sendParam = SendParam({
    ...
    amountLD: cleanedAmount,
    minAmountLD: minAmount,
    ...
});
(, OFTReceipt memory receipt) = oftAdapter.send{value: nativeFee}(sendParam, fee, msg.sender);
emit BridgedRsETHToL2(dstLzChainId, l2Receiver, receipt.amountSentLD, receipt.amountReceivedLD);
```

---

### Proof of Concept

1. L2 pool holds `1_000_000_000_000_000_000` wei (1 rsETH) to serve depositors.
2. Manager calls `L1Vault.bridgeRsETHToL2(amount = 999_999_999_876_543_211, ...)`.
3. OFT adapter cleans: `amountSentLD = 999_999_999_000_000_000` (dust = `876_543_211` wei retained in L1Vault).
4. L2 pool receives `999_999_999_000_000_000` instead of `999_999_999_876_543_211`.
5. After enough cycles the pool's balance falls below the next user's `rsETHAmount`, causing `safeTransfer` to revert.
6. All user deposits to the L2 pool are blocked until the manager manually tops up the pool. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/L1Vault.sol (L240-256)
```text
        IERC20(address(rsETH)).safeIncreaseAllowance(address(oftAdapter), amount);

        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(),
            amountLD: amount,
            minAmountLD: minAmount,
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = MessagingFee({ nativeFee: nativeFee, lzTokenFee: 0 });

        oftAdapter.send{ value: nativeFee }(sendParam, fee, msg.sender);

        emit BridgedRsETHToL2(dstLzChainId, l2Receiver, amount, minAmount);
```

**File:** contracts/L1VaultV2.sol (L318-334)
```text
        IERC20(address(rsETH)).safeIncreaseAllowance(address(oftAdapter), amount);

        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(),
            amountLD: amount,
            minAmountLD: minAmount,
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = MessagingFee({ nativeFee: nativeFee, lzTokenFee: 0 });

        oftAdapter.send{ value: nativeFee }(sendParam, fee, msg.sender);

        emit BridgedRsETHToL2(dstLzChainId, l2Receiver, amount, minAmount);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L237-243)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```
