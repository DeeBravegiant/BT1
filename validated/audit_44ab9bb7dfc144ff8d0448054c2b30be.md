The eNear contract is an external mainnet contract (referenced in `IENear.sol` line 4). I have enough context from the in-scope files to complete the analysis.

---

### Title
Double-Mint of eNear via Nonce Isolation Between `OmniBridge.finTransfer` and `ENearProxy.finaliseNearToEthTransfer` — (`evm/src/eNear/contracts/ENearProxy.sol`)

### Summary

`OmniBridge.finTransfer` mints eNear by calling `ENearProxy.mint()`, which constructs a **synthetic fake proof** keyed on an internal `currentReceiptId` counter and passes it to `eNear.finaliseNearToEthTransfer`. The eNear contract marks that synthetic receipt ID as consumed. The original NEAR receipt ID — embedded in the real NEAR proof — is never presented to eNear and therefore never marked used. A separate public entry point, `ENearProxy.finaliseNearToEthTransfer`, accepts the real NEAR proof, validates it via `prover.proveOutcome`, and calls `eNear.finaliseNearToEthTransfer` again. Because eNear's `usedEvents` mapping has no entry for the real receipt ID, it mints a second time. `ENearProxy.finaliseNearToEthTransfer` performs no cross-check against `OmniBridge.completedTransfers`.

### Finding Description

**`ENearProxy.mint` — synthetic proof path (called by OmniBridge):** [1](#0-0) 

`mint()` is restricted to `MINTER_ROLE` (OmniBridge), but it constructs a fake proof embedding `currentReceiptId` (a monotonically incrementing uint256 counter) and calls `eNear.finaliseNearToEthTransfer(fakeProofData, 0)`. The eNear contract parses the receipt ID from the proof bytes and records it in its internal `usedEvents` mapping. Only the synthetic ID `N` is consumed; the real NEAR receipt ID is never seen by eNear.

**`ENearProxy.finaliseNearToEthTransfer` — legacy real-proof path (public):** [2](#0-1) 

This function is public, gated only by a pause flag. It calls `prover.proveOutcome(proofData, proofBlockHeight)` and then `eNear.finaliseNearToEthTransfer(proofData, proofBlockHeight)` with the **real** NEAR proof. There is no check against `OmniBridge.completedTransfers`.

**`OmniBridge.finTransfer` — nonce deduplication (OmniBridge-only):** [3](#0-2) 

`completedTransfers[payload.destinationNonce]` prevents replay through the OmniBridge path, but this mapping is invisible to `ENearProxy.finaliseNearToEthTransfer`.

**Root cause:** Two independent deduplication domains exist for the same logical transfer event:
- OmniBridge deduplicates by `destinationNonce` (MPC-assigned integer).
- eNear deduplicates by receipt ID parsed from proof bytes.

`ENearProxy.mint` bridges them by using a fake proof with a synthetic receipt ID, leaving the real NEAR receipt ID unconsumed in eNear's `usedEvents`.

### Impact Explanation

An attacker who initiates a legitimate NEAR-to-ETH transfer receives eNear twice for a single NEAR-side lock:
1. Once via the OmniBridge relayer path (`finTransfer` → `ENearProxy.mint` → fake proof).
2. Once via the legacy path (`ENearProxy.finaliseNearToEthTransfer` → real proof → eNear mints again).

This creates unbacked eNear supply proportional to the attacker's transfer amount, directly violating the 1:1 backing invariant. The attacker retains the extra eNear and can redeem or sell it.

### Likelihood Explanation

The attack requires only:
1. A legitimate NEAR-side lock (the attacker initiates their own transfer).
2. Waiting for the relayer to call `OmniBridge.finTransfer`.
3. Calling the public `ENearProxy.finaliseNearToEthTransfer` with the original NEAR proof.

No privileged access, leaked keys, or external collusion is required. The `PAUSED_LEGACY_FIN_TRANSFER` flag is the only gate, and it is not set by default. The attack is repeatable for any amount.

### Recommendation

1. **Cross-check in `ENearProxy.finaliseNearToEthTransfer`:** Before calling `eNear.finaliseNearToEthTransfer`, verify that the transfer's `destinationNonce` (or an equivalent identifier derivable from the proof) is not already present in `OmniBridge.completedTransfers`.
2. **Alternatively, permanently pause `PAUSED_LEGACY_FIN_TRANSFER`** if the legacy path is no longer needed, since all new transfers flow through `OmniBridge.finTransfer` → `ENearProxy.mint`.
3. **Preferred long-term fix:** Replace the fake-proof mechanism in `ENearProxy.mint` with a direct privileged mint on eNear (bypassing `finaliseNearToEthTransfer` entirely), so eNear's `usedEvents` is not involved in the OmniBridge path and the two deduplication domains are fully decoupled.

### Proof of Concept

```
1. Attacker locks X NEAR on the NEAR side, generating real NEAR proof P
   with receipt ID R_real.

2. Relayer calls OmniBridge.finTransfer(sig, payload{destinationNonce=42, ...})
   → completedTransfers[42] = true
   → ENearProxy.mint(eNear, attacker, X)
       → fakeProof embeds currentReceiptId = N
       → eNear.finaliseNearToEthTransfer(fakeProof, 0)
           → eNear.usedEvents[N] = true
           → eNear mints X to attacker
       → currentReceiptId = N+1

3. Attacker calls ENearProxy.finaliseNearToEthTransfer(P, blockHeight)
   → prover.proveOutcome(P, blockHeight) == true  (real proof, passes)
   → eNear.finaliseNearToEthTransfer(P, blockHeight)
       → eNear parses receipt ID R_real from P
       → eNear.usedEvents[R_real] == false  (never set; only N was set)
       → eNear mints X to attacker AGAIN

4. assert eNear.totalSupply() increased by 2X; attacker holds 2X eNear
   backed by only X NEAR locked.
```

### Citations

**File:** evm/src/eNear/contracts/ENearProxy.sol (L51-73)
```text
    function mint(
        address token,
        address to,
        uint128 amount
    ) public onlyRole(MINTER_ROLE) {
        require(token == address(eNear), "ERR_INCORRECT_ENEAR_ADDRESS");

        bytes memory fakeProofData = bytes.concat(
            new bytes(72),
            hex"01000000",
            abi.encodePacked(currentReceiptId),
            new bytes(24),
            abi.encodePacked(Borsh.swapBytes4(uint32(nearConnector.length))),
            abi.encodePacked(nearConnector),
            hex"022500000000",
            abi.encodePacked(Borsh.swapBytes16(amount)),
            abi.encodePacked(to),
            new bytes(280)
        );

        currentReceiptId += 1;
        eNear.finaliseNearToEthTransfer(fakeProofData, 0);
    }
```

**File:** evm/src/eNear/contracts/ENearProxy.sol (L80-90)
```text
    function finaliseNearToEthTransfer(
        bytes memory proofData,
        uint64 proofBlockHeight
    ) external whenNotPaused(PAUSED_LEGACY_FIN_TRANSFER) {
        require(
            prover.proveOutcome(proofData, proofBlockHeight),
            "Proof should be valid"
        );

        eNear.finaliseNearToEthTransfer(proofData, proofBlockHeight);
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```
