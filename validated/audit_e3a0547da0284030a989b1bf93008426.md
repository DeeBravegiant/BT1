### Title
Non-Standard ERC-20 Metadata Encoding Permanently Blocks Token Bridging — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.logMetadata` is the required public entry point for registering an EVM-native token before it can be bridged. It calls `IERC20Metadata.name()`, `.symbol()`, and `.decimals()` with no `try/catch` and no fallback. Tokens such as MKR that return `bytes32` instead of `string` for name/symbol cause the Solidity ABI decoder to revert unconditionally, permanently blocking those tokens from ever being bridged.

### Finding Description

`logMetadata` at lines 224–232 of `OmniBridge.sol` performs three bare external calls:

```solidity
function logMetadata(address tokenAddress) external payable {
    string memory name    = IERC20Metadata(tokenAddress).name();
    string memory symbol  = IERC20Metadata(tokenAddress).symbol();
    uint8  decimals       = IERC20Metadata(tokenAddress).decimals();
    ...
}
``` [1](#0-0) 

The function carries no access-control modifier — it is `external payable` and callable by any address. [2](#0-1) 

`IERC20Metadata` expects `name()` and `symbol()` to return ABI-encoded `string`. Tokens such as MKR (`0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2`) return a raw `bytes32` value instead. When Solidity's ABI decoder attempts to decode a 32-byte word as a dynamic `string`, it reads the first word as a length offset, interprets the value as a pointer far outside the return buffer, and reverts with an out-of-bounds read. There is no `try/catch` and no low-level fallback, so the revert propagates unconditionally.

The same unchecked pattern appears in `addCustomToken` (the admin path), which also calls `IERC20Metadata` without protection:

```solidity
string memory name    = IERC20Metadata(tokenAddress).name();
string memory symbol  = IERC20Metadata(tokenAddress).symbol();
uint8  decimals       = IERC20Metadata(tokenAddress).decimals();
``` [3](#0-2) 

`logMetadata` is the mandatory first step in the EVM→NEAR bridging flow. Its emitted `LogMetadata` event is consumed by the NEAR side to produce a signed `MetadataPayload`, which is then submitted to `deployToken` to create the wrapped token. If `logMetadata` always reverts for a given token, no signed payload is ever produced, `deployToken` can never be called, and the token is permanently unbridgeable. [1](#0-0) 

The Starknet `log_metadata` uses `unwrap_syscall()` on every metadata call, which panics on any call failure, producing the same permanent block on that chain: [4](#0-3) 

### Impact Explanation

The bridging path for any ERC-20 token whose `name()` or `symbol()` does not return a standard ABI-encoded `string` is permanently frozen. No user or admin can register such a token through the public protocol interface. This falls under **Critical — frozen redemption path / permanently unclaimable user or protocol value**, because the entire bridge flow for affected tokens is irreversibly blocked at the metadata-registration gate.

### Likelihood Explanation

MKR is a canonical, high-value example (>$1 B market cap) of a `bytes32`-returning token. The pattern is documented and affects multiple deployed tokens. Any user attempting to bridge such a token will trigger the revert on every call, with no workaround available through unprivileged inputs.

### Recommendation

Wrap each metadata call in a `try/catch` (or use a low-level `staticcall` with manual ABI decoding) and fall back to a default value on failure. Additionally, attempt `bytes32` decoding when the standard `string` decode fails, mirroring the approach used by Polygon zkEVM and Arbitrum token bridges. Example pattern:

```solidity
function _safeTokenName(address token) internal view returns (string memory) {
    try IERC20Metadata(token).name() returns (string memory n) {
        return bytes(n).length > 0 ? n : "UNKNOWN";
    } catch {
        // Attempt bytes32 fallback (e.g. MKR)
        (bool ok, bytes memory data) = token.staticcall(abi.encodeWithSignature("name()"));
        if (ok && data.length == 32) {
            return _bytes32ToString(bytes32(data));
        }
        return "UNKNOWN";
    }
}
```

Apply the same pattern to `symbol()` and `decimals()`.

### Proof of Concept

1. Deploy or reference MKR on mainnet (`0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2`).
2. Call `OmniBridge.logMetadata(0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2)` from any EOA.
3. The call to `IERC20Metadata(tokenAddress).name()` returns 32 bytes (`bytes32`). Solidity's ABI decoder interprets the first word as a string-length offset, finds it points outside the return data, and reverts.
4. The `LogMetadata` event is never emitted; the NEAR side never receives a metadata payload; `deployToken` can never be called for MKR; MKR is permanently unbridgeable through Omni Bridge. [1](#0-0)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L99-101)
```text
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-232)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
    }
```

**File:** starknet/src/omni_bridge.cairo (L151-154)
```text
            let mut res = syscalls::call_contract_syscall(
                token, selector!("name"), call_data.span(),
            )
                .unwrap_syscall();
```
