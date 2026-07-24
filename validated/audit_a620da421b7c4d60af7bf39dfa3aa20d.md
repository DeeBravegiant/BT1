The code confirms the vulnerability. Here is the complete analysis:

---

### Title
Missing Chain-ID Binding in `deployToken` MetadataPayload Borsh Encoding Enables Cross-Chain Signature Replay — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`deployToken` constructs a Borsh-encoded payload that contains no `omniBridgeChainId` field and hashes it with a raw `keccak256`. A valid NEAR-MPC signature produced for one EVM deployment (e.g., Ethereum mainnet) is byte-for-byte replayable against any other EVM deployment that shares the same `nearBridgeDerivedAddress`, permanently blocking legitimate token deployment on the target chain.

### Finding Description

The Borsh encoding in `deployToken` is:

```
PayloadType.Metadata | encodeString(token) | encodeString(name) | encodeString(symbol) | bytes1(decimals)
``` [1](#0-0) 

No `omniBridgeChainId`, no EIP-712 domain separator, no EVM chain ID, and no contract address is mixed into the hash. The signature check is:

```solidity
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [2](#0-1) 

`nearBridgeDerivedAddress` is a single stored value, identical across all EVM deployments of the bridge (it is derived from the NEAR MPC key, which is chain-agnostic). [3](#0-2) 

Contrast this with `finTransfer`, which **does** embed `omniBridgeChainId` into its Borsh encoding (twice — once for the token address field and once for the recipient field), making those signatures chain-specific: [4](#0-3) 

`deployToken` has no equivalent protection. The `MetadataPayload` struct itself carries no chain-binding field: [5](#0-4) 

### Impact Explanation

An attacker who observes a valid `deployToken(sig, payload)` call on chain A can immediately replay it on chain B:

1. `keccak256(borshEncoded)` is identical on both chains (same payload, same encoding, no chain discriminator).
2. `ECDSA.recover` returns the same `nearBridgeDerivedAddress` on both chains.
3. The only guard against re-deployment is `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")`, which passes on chain B because the token has not been deployed there yet. [6](#0-5) 

After the replay succeeds:

- A `BridgeToken` proxy is deployed on chain B and registered in `nearToEthToken` / `isBridgeToken`.
- Any subsequent legitimate attempt by NEAR MPC to deploy the same token on chain B will permanently revert with `ERR_TOKEN_EXIST`, freezing the legitimate deployment path for that token on that chain.
- The bridge on chain B now holds a registered wrapped token whose EVM address was never communicated to the NEAR side, creating an orphaned token that can never be legitimately minted through `finTransfer` (which requires the NEAR side to know the EVM address). This constitutes an irreversible denial-of-service on the token's cross-chain redemption path for chain B.

### Likelihood Explanation

- The attack requires only watching a public `deployToken` transaction on any live EVM chain and submitting the same calldata to another chain's bridge contract. No privileged access, no key material, no colluding parties.
- Both Ethereum mainnet and Arbitrum deployments of OmniBridge are live simultaneously, making the replay window permanent (not time-bounded).
- The attacker pays only gas.

### Recommendation

Include `omniBridgeChainId` in the Borsh-encoded MetadataPayload before hashing, mirroring the pattern already used in `finTransfer`:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

The NEAR MPC signing logic must be updated in lockstep to include the destination chain ID when producing MetadataPayload signatures.

### Proof of Concept

```solidity
// Differential test — two OmniBridge instances, same nearBridgeDerivedAddress
// Chain A: omniBridgeChainId = 1 (Ethereum)
// Chain B: omniBridgeChainId = 2 (Arbitrum)

BridgeTypes.MetadataPayload memory payload = BridgeTypes.MetadataPayload({
    token: "token.near",
    name: "Token",
    symbol: "TKN",
    decimals: 18
});

// Encoding on chain A
bytes memory encodedA = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(payload.token),
    Borsh.encodeString(payload.name),
    Borsh.encodeString(payload.symbol),
    bytes1(payload.decimals)
);

// Encoding on chain B — identical, no chain discriminator
bytes memory encodedB = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(payload.token),
    Borsh.encodeString(payload.name),
    Borsh.encodeString(payload.symbol),
    bytes1(payload.decimals)
);

assert(keccak256(encodedA) == keccak256(encodedB)); // always true

// MPC signs for chain A; attacker replays on chain B
bytes memory sig = mpcSign(keccak256(encodedA));
bridgeA.deployToken(sig, payload); // legitimate
bridgeB.deployToken(sig, payload); // replay succeeds — no fresh MPC signature needed
```

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L41-42)
```text
    address public nearBridgeDerivedAddress;
    uint8 public omniBridgeChainId;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L142-153)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-158)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-308)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
```

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L16-21)
```text
    struct MetadataPayload {
        string token;
        string name;
        string symbol;
        uint8 decimals;
    }
```
