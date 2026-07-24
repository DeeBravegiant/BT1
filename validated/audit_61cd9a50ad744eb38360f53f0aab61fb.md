### Title
Native-fee ETH permanently locked in EVM bridge with no withdrawal path - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

Every call to `OmniBridge.initTransfer` (or `initTransfer1155`) that includes a non-zero `nativeFee` causes that ETH to be retained inside the contract with no mechanism to ever recover it. The relayer is compensated on NEAR via minted wrapped-ETH tokens, but the actual ETH accumulates permanently in the EVM bridge contract.

---

### Finding Description

In `OmniBridge.initTransfer`, the `msg.value` split is:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol  lines 387-413
if (tokenAddress == address(0)) {
    extensionValue = msg.value - amount - nativeFee;   // ETH bridge path
} else {
    extensionValue = msg.value - nativeFee;            // ERC-20 path
    ...
}
initTransferExtension(..., extensionValue);
``` [1](#0-0) 

`extensionValue` — which equals `msg.value - nativeFee` — is the only ETH forwarded to `initTransferExtension`. In `OmniBridgeWormhole`, that value is consumed entirely by the Wormhole publish fee:

```solidity
// evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol  lines 143-144
_wormhole.publishMessage{value: value}(wormholeNonce, payload, _consistencyLevel);
``` [2](#0-1) 

The `nativeFee` slice of `msg.value` is never forwarded anywhere. It silently accumulates in the contract's ETH balance. The contract exposes only a bare `receive() external payable {}` and has no `withdraw`, `rescueETH`, or equivalent function. [3](#0-2) 

On the NEAR side, `send_fee_internal` compensates the relayer by **minting** wrapped-ETH tokens — it does not redeem the locked ETH from the EVM contract:

```rust
// near/omni-bridge/src/lib.rs  lines 2673-2677
ext_token::ext(self.get_native_token_id(origin_chain))
    .with_static_gas(MINT_TOKEN_GAS)
    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
    .detach();
``` [4](#0-3) 

The same pattern applies to `initTransfer1155`: [5](#0-4) 

---

### Impact Explanation

Every `initTransfer` / `initTransfer1155` call with `nativeFee > 0` permanently locks that ETH in the bridge contract. There is no admin withdrawal path, no rescue function, and no upgrade-triggered drain in the current code. The value is irreversibly unclaimable protocol fee revenue. This matches the **Critical** impact class: *permanently unclaimable protocol value in bridge fee flows*.

---

### Likelihood Explanation

`nativeFee` is a documented, first-class feature of the bridge (README explicitly lists "Native chain token (e.g., ETH, SOL)" as a valid fee currency). Any ordinary user who pays a native fee on EVM triggers the lock. No special role or privileged access is required — the entry point is the public `initTransfer` function.

---

### Recommendation

Add an admin-only ETH withdrawal function to `OmniBridge.sol`, for example:

```solidity
function withdrawNativeFees(address payable to, uint256 amount)
    external onlyRole(DEFAULT_ADMIN_ROLE)
{
    (bool ok,) = to.call{value: amount}("");
    require(ok, "ETH transfer failed");
}
```

Alternatively, track accumulated `nativeFee` in a dedicated storage variable and allow the DAO role to sweep it, mirroring how the NEAR side tracks and distributes fee balances.

---

### Proof of Concept

1. User calls `OmniBridge.initTransfer(tokenAddress, 1000, 10, 1e17, "near:recipient", "")` with `msg.value = 1e17` (0.1 ETH as native fee) for an ERC-20 transfer.
2. Inside `initTransfer`: `extensionValue = 1e17 - 1e17 = 0`. The `nativeFee` of `1e17` wei stays in the contract.
3. `initTransferExtension` is called with `value = 0`; Wormhole receives nothing (or the Wormhole fee is zero).
4. On NEAR, the relayer calls `claim_fee`; `send_fee_internal` mints wrapped-ETH tokens to the relayer — no ETH leaves the EVM contract.
5. Repeat for every bridging user who pays a native fee. The contract's ETH balance grows monotonically with no withdrawal path. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
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

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L466-466)
```text
        uint256 extensionValue = msg.value - nativeFee;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-149)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
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

        wormholeNonce++;
```

**File:** near/omni-bridge/src/lib.rs (L2655-2707)
```rust
    fn send_fee_internal(
        &mut self,
        transfer_message: &TransferMessage,
        fee_recipient: AccountId,
        token_fee: u128,
    ) -> PromiseOrValue<()> {
        if transfer_message.fee.native_fee.0 != 0 {
            let origin_chain = transfer_message.origin_transfer_id.as_ref().map_or_else(
                || transfer_message.get_origin_chain(),
                |origin_transfer_id| origin_transfer_id.origin_chain,
            );

            if origin_chain.is_utxo_chain() {
                env::panic_str(BridgeError::NativeFeeForUtxoChain.to_string().as_str())
            } else if origin_chain == ChainKind::Near {
                Promise::new(fee_recipient.clone())
                    .transfer(NearToken::from_yoctonear(transfer_message.fee.native_fee.0))
                    .detach();
            } else {
                ext_token::ext(self.get_native_token_id(origin_chain))
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
        }

        let token = self.get_token_id(&transfer_message.token);
        env::log_str(
            &OmniBridgeEvent::ClaimFeeEvent {
                transfer_message: transfer_message.clone(),
            }
            .to_log_string(),
        );

        self.unlock_tokens_if_needed(transfer_message.get_destination_chain(), &token, token_fee);

        if token_fee > 0 {
            if self.is_deployed_token(&token) {
                ext_token::ext(token)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient, U128(token_fee), None)
                    .into()
            } else {
                ext_token::ext(token)
                    .with_static_gas(FT_TRANSFER_GAS)
                    .with_attached_deposit(ONE_YOCTO)
                    .ft_transfer(fee_recipient, U128(token_fee), None)
                    .into()
            }
        } else {
            PromiseOrValue::Value(())
        }
    }
```
