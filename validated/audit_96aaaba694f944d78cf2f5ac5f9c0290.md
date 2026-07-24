### Title
Fee-on-Transfer Token Accounting Divergence Causes Irreversible Fund Lock on EVM Bridge - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`OmniBridge.initTransfer()` accepts any arbitrary ERC20 token and emits the caller-supplied `amount` in the `InitTransfer` event without checking the actual balance received. For fee-on-transfer tokens, the contract receives `amount - fee` but the NEAR side credits the full `amount`. When users later bridge back, `finTransfer` attempts to release the full `amount` but the contract is short by the cumulative fee, causing the last users' redemptions to revert permanently.

### Finding Description

`OmniBridge.initTransfer()` is a public, permissionless function. For any ERC20 token that is neither a bridge-deployed token nor a custom-minter token, the code path is:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount
    );
}
```

followed immediately by:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // <-- caller-supplied, not actual received
    fee,
    nativeFee,
    recipient,
    message
);
```

No balance-before/after check is performed. The emitted `amount` is the caller-supplied parameter, not the actual tokens received by the contract. [1](#0-0) [2](#0-1) 

The NEAR bridge's `fin_transfer_callback` processes the proof of this EVM `InitTransfer` event and constructs a `TransferMessage` using `init_transfer.amount` directly from the prover result (which reflects the event's `amount` field): [3](#0-2) 

This means NEAR credits the user with the full `amount`, while the EVM bridge only holds `amount - fee`.

When the user bridges back (NEAR → EVM), `finTransfer` is called with `payload.amount` equal to the original over-credited `amount` and attempts:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [4](#0-3) 

The contract's actual balance is `amount - fee` (or less, if multiple users have bridged the same fee-on-transfer token). The `safeTransfer` reverts for the last users, permanently locking their funds.

There is no token whitelist for the "else" branch. Any ERC20 token — including fee-on-transfer tokens — can be passed as `tokenAddress` to `initTransfer`. The `logMetadata` function is also permissionless and can be used to register any token with the NEAR bridge before bridging. [5](#0-4) 

### Impact Explanation

**Critical — Irreversible fund lock / frozen redemption path.**

For every `initTransfer` call with a fee-on-transfer token, the EVM bridge accumulates a deficit equal to the transfer fee. The NEAR side has credited more tokens than the EVM bridge holds. When users attempt to redeem on EVM via `finTransfer`, the last users in the queue will have their transactions revert with insufficient balance. Their NEAR-side tokens are burned/locked but the EVM-side release fails, permanently destroying user value with no recovery path.

### Likelihood Explanation

**Medium.** The `initTransfer` function is fully permissionless — no admin approval, no token whitelist, no registration requirement for the "else" branch. Any user can call it with any ERC20 token address. Fee-on-transfer tokens (e.g., PAXG, STA, tokens with deflationary mechanics) are a well-known token class. A single user bridging such a token is sufficient to trigger the accounting divergence; the impact compounds with each additional user bridging the same token.

### Recommendation

1. **Balance-check pattern**: Record the contract's token balance before and after `safeTransferFrom` and use the difference as the actual `amount` to emit in the event:
   ```solidity
   uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
   IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
   uint256 actualAmount = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
   // use actualAmount in the event and downstream logic
   ```

2. **Token allowlist**: Restrict the "else" branch to only tokens that have been explicitly registered by an admin, preventing arbitrary ERC20 tokens from being bridged without vetting.

### Proof of Concept

1. Deploy or use an existing fee-on-transfer ERC20 token `FeeToken` with a 1% transfer fee.
2. Call `OmniBridge.initTransfer(address(FeeToken), 1000, 0, 0, "near:alice.near", "")` with 1000 tokens approved.
3. The contract receives 990 tokens (1% fee deducted), but emits `InitTransfer(..., amount=1000, ...)`.
4. The NEAR bridge processes the proof of this event and credits `alice.near` with 1000 FeeToken-equivalent wrapped tokens.
5. Alice bridges back: NEAR burns her 1000 tokens and calls `finTransfer` on EVM with `payload.amount = 1000`.
6. `IERC20(FeeToken).safeTransfer(alice, 1000)` reverts — the contract only holds 990.
7. Alice's funds are permanently locked: burned on NEAR, unredeemable on EVM. [6](#0-5) [7](#0-6)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

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

**File:** near/omni-bridge/src/lib.rs (L704-736)
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

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };
```
