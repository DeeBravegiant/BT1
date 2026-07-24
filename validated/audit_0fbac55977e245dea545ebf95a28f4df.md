### Title
Non-UTF-8 `message` in `initTransfer` Causes Permanent Fund Lock via Borsh Deserialization Failure — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`, `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs`)

---

### Summary

An unprivileged user can call `OmniBridge.initTransfer` with a `message` parameter containing invalid UTF-8 byte sequences (e.g., `0xFF`). Solidity does not validate UTF-8 in `string` types, so the bytes pass through `Borsh.encodeString` unchanged into the Wormhole VAA payload. On the NEAR side, `InitTransferWh.message` is typed as Rust `String`, and `borsh::from_slice` enforces UTF-8 validity during deserialization. The parse fails, the prover returns an error, and the transfer can never be claimed — while the tokens are already burned or locked on the EVM side.

---

### Finding Description

**Step 1 — EVM entrypoint, no UTF-8 validation.**

`OmniBridge.initTransfer` accepts `string calldata message` with no validation: [1](#0-0) 

Solidity's `string` type is raw bytes; the EVM ABI encoder does not enforce UTF-8. Any byte sequence is accepted.

**Step 2 — Tokens are burned/locked before the VAA is published.**

Before `initTransferExtension` is called, the token transfer is already executed: [2](#0-1) 

The funds leave the user's wallet at this point, unconditionally.

**Step 3 — `Borsh.encodeString` passes raw bytes verbatim.**

`encodeString` casts `string` to `bytes` and prepends a 4-byte little-endian length — no UTF-8 check: [3](#0-2) 

**Step 4 — The VAA payload is published with the invalid bytes.**

`initTransferExtension` encodes `message` via `Borsh.encodeString(message)` and publishes it to Wormhole: [4](#0-3) 

**Step 5 — NEAR prover deserializes `message` as Rust `String`.**

`InitTransferWh` declares `message: String`: [5](#0-4) 

Rust's `borsh` crate deserializes `String` by reading the length-prefixed bytes and calling `String::from_utf8()`. If any byte is invalid UTF-8 (e.g., `0xFF`), this returns `Err`.

**Step 6 — Deserialization failure propagates as a permanent prover error.**

```rust
let transfer: InitTransferWh = borsh::from_slice(&self.payload).map_err(stringify)?;
``` [6](#0-5) 

The `?` propagates the error back through `verify_vaa_callback`: [7](#0-6) 

The prover returns `Err`, the NEAR bridge's `fin_transfer` call fails, and there is no retry or recovery path — the VAA is valid and signed by Wormhole guardians, but the payload is permanently undeserializable.

---

### Impact Explanation

**Critical — Irreversible fund lock.**

Tokens are burned (bridge tokens) or transferred to the contract (native ERC-20s) on the EVM side before the VAA is emitted. The NEAR prover will always reject the VAA payload for this transfer. There is no mechanism to re-submit with a corrected message, and the Wormhole sequence number is consumed. The transfer is permanently unclaimable.

---

### Likelihood Explanation

**Medium.** The attack requires only a single public call to `initTransfer` with a crafted `message` argument. No privileged role, no key, no colluding party is needed. A malicious user can grief any transfer of their own funds, or a confused user can accidentally trigger this with copy-pasted binary data. The attack is cheap (one transaction) and irreversible.

---

### Recommendation

Validate UTF-8 on the EVM side before encoding. Since Solidity cannot natively validate UTF-8, the simplest fix is to change the `message` parameter type from `string` to `bytes` throughout the call chain (`initTransfer`, `initTransferExtension`, `Borsh.encodeString`), and on the NEAR side change `InitTransferWh.message` from `String` to `Vec<u8>` (and `InitTransferMessage.msg` accordingly). Alternatively, add an explicit UTF-8 validity check in a Solidity helper before encoding. The same issue applies to the `recipient` field in `InitTransferWh` and to `name`/`symbol` in `LogMetadataWh` and `token` in `DeployTokenWh`.

---

### Proof of Concept

```rust
// Rust unit test demonstrating the failure
#[test]
fn test_invalid_utf8_message_fails_borsh() {
    // Simulate Borsh.encodeString("\xff") from Solidity:
    // 4-byte LE length = 1, then 0xFF
    let encoded: Vec<u8> = vec![0x01, 0x00, 0x00, 0x00, 0xFF];
    let result = borsh::from_slice::<String>(&encoded);
    assert!(result.is_err(), "borsh must reject invalid UTF-8 in String");
}
```

On the EVM side, the attacker calls:
```solidity
bridge.initTransfer{value: nativeFee}(
    tokenAddress,
    amount,
    fee,
    nativeFee,
    "near:alice.near",
    "\xff"          // single invalid UTF-8 byte — Solidity accepts it
);
```

Tokens are burned. The Wormhole VAA is published. The NEAR prover panics on `borsh::from_slice::<InitTransferWh>`. The transfer is permanently locked.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-380)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L404-413)
```text
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```

**File:** evm/src/common/Borsh.sol (L17-27)
```text
    function encodeString(
        string memory val
    ) internal pure returns (bytes memory) {
        return encodeBytes(bytes(val));
    }

    function encodeBytes(
        bytes memory val
    ) internal pure returns (bytes memory) {
        return bytes.concat(encodeUint32(uint32(val.length)), val);
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L129-148)
```text
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L143-154)
```rust
#[derive(Debug, BorshDeserialize)]
struct InitTransferWh {
    payload_type: ProofKind,
    sender: OmniAddress,
    token_address: OmniAddress,
    origin_nonce: Nonce,
    amount: u128,
    fee: u128,
    native_fee: u128,
    recipient: String,
    message: String,
}
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L159-161)
```rust
    fn try_into(self) -> Result<InitTransferMessage, String> {
        let transfer: InitTransferWh = borsh::from_slice(&self.payload).map_err(stringify)?;

```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs (L79-84)
```rust
        match proof_kind {
            ProofKind::InitTransfer => Ok(ProverResult::InitTransfer(parsed_vaa.try_into()?)),
            ProofKind::FinTransfer => Ok(ProverResult::FinTransfer(parsed_vaa.try_into()?)),
            ProofKind::DeployToken => Ok(ProverResult::DeployToken(parsed_vaa.try_into()?)),
            ProofKind::LogMetadata => Ok(ProverResult::LogMetadata(parsed_vaa.try_into()?)),
        }
```
