### Title
Unrestricted `logMetadata` Allows Any Caller to Publish Arbitrary Wormhole Token-Metadata Messages — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.logMetadata` is declared `external payable` with **no access control**. In the `OmniBridgeWormhole` deployment it calls `logMetadataExtension`, which unconditionally publishes a Wormhole message whose content is read entirely from the caller-supplied `tokenAddress`. An attacker deploys a malicious ERC20 stub that returns arbitrary `name`, `symbol`, and `decimals`, then calls `logMetadata` to inject a forged `LogMetadata` Wormhole VAA into the bridge's cross-chain messaging layer.

---

### Finding Description

`OmniBridge.logMetadata` at line 224 of `evm/src/omni-bridge/contracts/OmniBridge.sol`:

```solidity
function logMetadata(address tokenAddress) external payable {
    string memory name  = IERC20Metadata(tokenAddress).name();
    string memory symbol = IERC20Metadata(tokenAddress).symbol();
    uint8  decimals = IERC20Metadata(tokenAddress).decimals();
    logMetadataExtension(tokenAddress, name, symbol, decimals);
    emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
}
``` [1](#0-0) 

There is no `onlyRole`, `whenNotPaused`, or any other guard. Every field of the resulting message is sourced from the attacker-controlled `tokenAddress`.

In `OmniBridgeWormhole`, `logMetadataExtension` is overridden to publish a Wormhole message:

```solidity
function logMetadataExtension(...) internal override {
    bytes memory payload = bytes.concat(
        bytes1(uint8(MessageType.LogMetadata)),
        bytes1(omniBridgeChainId),
        Borsh.encodeAddress(tokenAddress),
        Borsh.encodeString(name),
        Borsh.encodeString(symbol),
        bytes1(decimals)
    );
    _wormhole.publishMessage{value: msg.value}(wormholeNonce, payload, _consistencyLevel);
    wormholeNonce++;
}
``` [2](#0-1) 

The attacker fully controls all five fields of the published VAA payload.

The same pattern exists on Starknet: `log_metadata` in `starknet/src/omni_bridge.cairo` is also callable by any account with no role check, and emits a `LogMetadata` event that relayers forward to NEAR. [3](#0-2) 

---

### Impact Explanation

The NEAR bridge uses `token_decimals` as the authoritative source for amount normalization in both `fin_transfer_callback` and `sign_transfer`:

```rust
let decimals = self
    .token_decimals
    .get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
let amount_to_transfer = Self::normalize_amount(..., decimals);
``` [4](#0-3) [5](#0-4) 

A `LogMetadata` VAA is the mechanism by which EVM token metadata (including `decimals`) is registered or updated on NEAR. If an attacker publishes a forged VAA for an already-registered token with a manipulated `decimals` value, the NEAR bridge's normalization arithmetic diverges from the true on-chain supply, breaking the 1:1 backing guarantee — a **High** asset-identity / decimals-divergence impact. If the target token has not yet been registered, the attacker can pre-register it with wrong decimals before the legitimate owner does, permanently poisoning the mapping.

---

### Likelihood Explanation

The entry point is a plain `external payable` function requiring no role, no deposit beyond Wormhole's `messageFee`, and no prior state. Any EOA can execute the attack in a single transaction. The attacker only needs to deploy a minimal ERC20 stub (three view functions) and pay the Wormhole message fee. Likelihood is **High**.

---

### Recommendation

Add an access-control guard to `logMetadata` (and `logMetadata1155`) so that only the token contract itself, a registered bridge operator, or an account holding a designated role can trigger metadata publication:

```solidity
function logMetadata(address tokenAddress)
    external payable
    onlyRole(DEFAULT_ADMIN_ROLE)   // or a dedicated METADATA_REPORTER_ROLE
{
    ...
}
```

Apply the same restriction to the Starknet `log_metadata` entry point.

---

### Proof of Concept

1. Attacker deploys `MaliciousToken` implementing `IERC20Metadata`:
   - `name()` → `"Wrapped ETH"`
   - `symbol()` → `"WETH"`
   - `decimals()` → `6` (true WETH uses 18)
2. Attacker calls `OmniBridgeWormhole.logMetadata{value: wormholeFee}(address(maliciousToken))`.
3. `logMetadataExtension` publishes a Wormhole VAA: `[LogMetadata | chainId | maliciousToken | "Wrapped ETH" | "WETH" | 6]`.
4. A Wormhole relayer delivers the VAA to NEAR; the NEAR bridge registers (or updates) the token with `decimals = 6`.
5. When a user later bridges 1 WETH (1e18 wei) from EVM, NEAR normalizes using `decimals = 6` instead of 18, crediting the user with `1e12` times the correct amount — or, in the reverse direction, minting 1e12× more tokens than were locked on EVM, creating unbacked supply. [1](#0-0) [2](#0-1) [6](#0-5)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L72-94)
```text
    function logMetadataExtension(
        address tokenAddress,
        string memory name,
        string memory symbol,
        uint8 decimals
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.LogMetadata)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeString(name),
            Borsh.encodeString(symbol),
            bytes1(decimals)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```

**File:** starknet/src/omni_bridge.cairo (L144-200)
```text
        fn log_metadata(ref self: ContractState, token: ContractAddress) {
            // There are two possible metadata standards in use.
            // 1. Old style: name and symbol are felt252 values.
            // 2. New style: name and symbol are ByteArray values (ERC20 ABI).
            // We are using low-level contract calls to determine the type.

            let call_data: Array<felt252> = array![];
            let mut res = syscalls::call_contract_syscall(
                token, selector!("name"), call_data.span(),
            )
                .unwrap_syscall();

            let name = if res.len() == 1 {
                // Old standard (felt252)
                let name = OptionTrait::expect(
                    Serde::<felt252>::deserialize(ref res), 'Could not deserialize name',
                );
                utils::felt252_to_string(name)
            } else {
                // New standard (ByteArray)
                OptionTrait::expect(
                    Serde::<ByteArray>::deserialize(ref res), 'Could not deserialize name',
                )
            };

            let mut res = syscalls::call_contract_syscall(
                token, selector!("symbol"), call_data.span(),
            )
                .unwrap_syscall();

            let symbol = if res.len() == 1 {
                // Old standard (felt252)
                let symbol = OptionTrait::expect(
                    Serde::<felt252>::deserialize(ref res), 'Could not deserialize symbol',
                );
                utils::felt252_to_string(symbol)
            } else {
                // New standard (ByteArray)
                OptionTrait::expect(
                    Serde::<ByteArray>::deserialize(ref res), 'Could not deserialize symbol',
                )
            };

            let decimals = {
                let mut res = syscalls::call_contract_syscall(
                    token, selector!("decimals"), call_data.span(),
                )
                    .unwrap_syscall();

                let decimals = OptionTrait::expect(
                    Serde::<u8>::deserialize(ref res), 'Could not deserialize decimals',
                );
                decimals
            };

            self.emit(Event::LogMetadata(LogMetadata { address: token, name, symbol, decimals }))
        }
```

**File:** near/omni-bridge/src/lib.rs (L475-484)
```rust
        let decimals = self
            .token_decimals
            .get(&token_address)
            .near_expect(BridgeError::TokenDecimalsNotFound);
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );
```

**File:** near/omni-bridge/src/lib.rs (L719-730)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
```
