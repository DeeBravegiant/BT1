### Title
Unregistered ERC-20 tokens locked permanently in EVM bridge via `initTransfer` with no recovery path — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`OmniBridge.initTransfer()` accepts any ERC-20 `tokenAddress` and locks it in the bridge contract without verifying that the token is registered on the NEAR side. When the NEAR `fin_transfer_callback` later attempts to look up decimals for the unregistered token, it panics and the transfer is never completed. Because the EVM contract has no rescue or withdrawal function for locked ERC-20 tokens, the user's funds are permanently stuck.

### Finding Description
`initTransfer` on the EVM bridge handles three token categories:

1. Custom-minter tokens → transferred to minter and burned
2. Bridge tokens (`isBridgeToken[tokenAddress] == true`) → burned via `BridgeToken.burn`
3. Everything else → `safeTransferFrom(msg.sender, address(this), amount)` — tokens locked in the bridge [1](#0-0) 

There is no check that the token in category 3 has a corresponding registration on the NEAR side (i.e., an entry in `token_decimals`, `token_id_to_address`, or `token_address_to_id`).

On the NEAR side, `fin_transfer_callback` unconditionally calls:

```rust
let decimals = self
    .token_decimals
    .get(&init_transfer.token)
    .near_expect(BridgeError::TokenDecimalsNotFound);
``` [2](#0-1) 

If the token was never registered via `deploy_token` / `bind_token`, this panics and the NEAR-side transaction reverts. The EVM-side lock is already committed and irreversible.

`OmniBridge.sol` contains no ERC-20 rescue or withdrawal function. The only outbound token path is `finTransfer`, which requires a valid MPC signature produced by the NEAR side — a signature that can never be produced for an unregistered token. [3](#0-2) 

The same pattern exists in the Starknet bridge: `init_transfer` accepts any `token_address` and locks it without checking NEAR-side registration. [4](#0-3) 

### Impact Explanation
Any user who calls `initTransfer` with a token that has not been registered on the NEAR side (i.e., has not gone through `logMetadata` → NEAR `deploy_token`/`bind_token`) will have their tokens permanently locked in the EVM bridge contract with no on-chain recovery path. This matches the **Critical** impact category: *Irreversible fund lock, frozen redemption path, or permanently unclaimable user value in bridge flows*.

The NEAR contract does have a DAO-only `transfer_token_as_dao` escape hatch for tokens held on the NEAR side, but there is no equivalent on the EVM side. [5](#0-4) 

### Likelihood Explanation
Any ERC-20 token that has not completed the `logMetadata` → NEAR `deploy_token`/`bind_token` registration flow is affected. A user who holds a legitimate but unregistered ERC-20 (e.g., a newly listed token, a token whose registration was never submitted, or a token whose NEAR-side deployment failed) can trigger this by calling the public `initTransfer` entrypoint. No privileged access is required.

### Recommendation
Add a registration check in `initTransfer` before locking native ERC-20 tokens:

```solidity
} else {
+   require(bytes(ethToNearToken[tokenAddress]).length > 0, "ERR_TOKEN_NOT_REGISTERED");
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount
    );
}
```

Alternatively, add an admin ERC-20 rescue function (analogous to NEAR's `transfer_token_as_dao`) so that tokens locked for unregistered tokens can be returned to their owners.

Apply the same fix to the Starknet `init_transfer`.

### Proof of Concept

1. Deploy or use any ERC-20 token `T` that has **not** been registered on the NEAR side (no `logMetadata` → `bind_token` flow completed for `T`).
2. Approve the EVM `OmniBridge` to spend `T`.
3. Call `OmniBridge.initTransfer(address(T), amount, 0, 0, "alice.near", "")`.
4. `safeTransferFrom` succeeds; `T` is now held by the bridge. `InitTransfer` event is emitted.
5. A relayer submits the proof to NEAR `fin_transfer`.
6. `fin_transfer_callback` reaches `token_decimals.get(&init_transfer.token).near_expect(BridgeError::TokenDecimalsNotFound)` and panics.
7. The NEAR transaction reverts. No MPC signature is ever produced for this transfer.
8. `T` is permanently locked in the EVM bridge — `finTransfer` cannot release it without a NEAR-side signature, and no such signature can be produced for an unregistered token. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-367)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

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
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }

        finTransferExtension(payload);

        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-413)
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
```

**File:** near/omni-bridge/src/lib.rs (L704-722)
```rust
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```

**File:** near/omni-bridge/src/lib.rs (L1517-1535)
```rust
    pub fn transfer_token_as_dao(
        &mut self,
        token: AccountId,
        amount: U128,
        recipient: AccountId,
        msg: Option<String>,
    ) -> Promise {
        if let Some(msg) = msg {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_CALL_GAS)
                .ft_transfer_call(recipient, amount, None, msg)
        } else {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        }
    }
```

**File:** starknet/src/omni_bridge.cairo (L281-331)
```text
        fn init_transfer(
            ref self: ContractState,
            token_address: ContractAddress,
            amount: u128,
            fee: u128,
            native_fee: u128,
            recipient: ByteArray,
            message: ByteArray,
        ) {
            assert(!_is_paused(@self, PAUSE_INIT_TRANSFER), 'ERR_INIT_TRANSFER_PAUSED');

            assert(amount > 0, 'ERR_ZERO_AMOUNT');
            assert(fee < amount, 'ERR_INVALID_FEE');

            let origin_nonce = self.current_origin_nonce.read() + 1;
            self.current_origin_nonce.write(origin_nonce);

            let caller = get_caller_address();

            if self.is_bridge_token(token_address) {
                IBridgeTokenDispatcher { contract_address: token_address }
                    .burn(caller, amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }

            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }

            self
                .emit(
                    Event::InitTransfer(
                        InitTransfer {
                            sender: caller,
                            token_address,
                            origin_nonce,
                            amount,
                            fee,
                            native_fee,
                            recipient,
                            message,
                        },
                    ),
                )
        }
```
