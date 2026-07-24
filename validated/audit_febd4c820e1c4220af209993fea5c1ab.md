### Title
Malicious ERC1155 Contract Can Emit `InitTransfer` Without Actual Token Custody — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`initTransfer1155()` in `OmniBridge.sol` calls `IERC1155(tokenAddress).safeTransferFrom()` on an arbitrary, caller-supplied address without verifying that the bridge's token balance actually increased. A malicious ERC1155 contract can return from `safeTransferFrom` without transferring any tokens, causing `InitTransfer` to be emitted. The NEAR side treats this event as sole proof of locked assets and mints unbacked wrapped tokens to the attacker, breaking the 1:1 backing guarantee.

### Finding Description

`initTransfer1155()` accepts an arbitrary `tokenAddress` with no whitelist or registration check: [1](#0-0) 

The critical sequence is:

1. The bridge calls `IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "")` — an external call to an attacker-controlled contract.
2. After that call returns (regardless of whether tokens were actually transferred), the bridge unconditionally emits `InitTransfer`. [2](#0-1) 

The bridge's `onERC1155Received` hook is declared `view` and only checks `operator == address(this)`: [3](#0-2) 

A malicious ERC1155 contract's `safeTransferFrom` can:
- Skip any actual token transfer
- Optionally call `bridge.onERC1155Received(address(bridge), ...)` directly (the `view` function changes no state and just returns the selector)
- Return normally without reverting

The bridge never checks its own ERC1155 balance after the call. The `InitTransfer` event is emitted unconditionally.

The NEAR side's security model explicitly relies solely on `InitTransfer` events as proof of locked assets: [4](#0-3) 

The NEAR bridge's `fin_transfer_callback` processes these events and mints/releases tokens based on them: [5](#0-4) 

Additionally, `initTransfer1155` has no reentrancy guard. A malicious ERC1155 can reenter `initTransfer1155` during the `safeTransferFrom` callback, generating multiple `InitTransfer` events with distinct nonces — each causing the NEAR side to mint additional unbacked wrapped tokens.

### Impact Explanation

The 1:1 backing guarantee is broken: the NEAR side mints wrapped ERC1155 tokens that have no corresponding locked assets on EVM. The attacker receives unbacked wrapped tokens on NEAR. Any user or protocol that accepts these wrapped tokens at face value (e.g., a DEX, liquidity pool, or fast-transfer relayer on NEAR) would be defrauded. The `logMetadata1155` function is also permissionless, so the attacker can register the fake token without any admin interaction: [6](#0-5) 

This maps to: **High — asset-identity / balance-accounting divergence that breaks backing guarantees.**

### Likelihood Explanation

Any unprivileged user can call `initTransfer1155` with an arbitrary ERC1155 address. No special role, leaked key, or privileged access is required. Deploying a malicious ERC1155 contract is trivial. The attack is fully self-contained and repeatable.

### Recommendation

After calling `safeTransferFrom`, verify that the bridge's balance of `(tokenAddress, tokenId)` increased by at least `amount`:

```solidity
uint256 balanceBefore = IERC1155(tokenAddress).balanceOf(address(this), tokenId);
IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "");
uint256 balanceAfter = IERC1155(tokenAddress).balanceOf(address(this), tokenId);
require(balanceAfter - balanceBefore >= amount, "ERC1155 transfer not received");
```

Alternatively, maintain an admin-controlled whitelist of approved ERC1155 token addresses, consistent with how `isBridgeToken` and `customMinters` gate ERC20 paths.

### Proof of Concept

```solidity
contract MaliciousERC1155 {
    // Implements IERC1155 interface but safeTransferFrom does nothing
    function safeTransferFrom(
        address, address, uint256, uint256, bytes calldata
    ) external {
        // No token transfer — just return
    }
    // ... minimal ERC165/IERC1155 stubs
}

// Attack:
// 1. Deploy MaliciousERC1155
MaliciousERC1155 fake = new MaliciousERC1155();

// 2. Register the fake token (permissionless)
bridge.logMetadata1155(address(fake), tokenId);
// → NEAR side deploys a wrapped token for the deterministic address

// 3. Call initTransfer1155 with the fake token
bridge.initTransfer1155(
    address(fake),
    tokenId,
    1_000_000,   // amount — no tokens actually held
    0, 0,
    "attacker.near",
    ""
);
// → MaliciousERC1155.safeTransferFrom transfers nothing
// → Bridge emits InitTransfer(sender, deterministicToken, nonce, 1_000_000, ...)
// → NEAR side mints 1_000_000 unbacked wrapped tokens to attacker.near
// → Bridge holds 0 ERC1155 tokens; NEAR supply is unbacked
```

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L234-270)
```text
    function logMetadata1155(
        address tokenAddress,
        uint256 tokenId
    ) external payable {
        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        MultiTokenInfo storage multiToken = multiTokens[deterministicToken];

        if (multiToken.tokenAddress == address(0)) {
            multiToken.tokenAddress = tokenAddress;
            multiToken.tokenId = tokenId;
        } else {
            if (
                multiToken.tokenAddress != tokenAddress ||
                multiToken.tokenId != tokenId
            ) {
                revert ERC1155MappingMismatch();
            }
        }

        logMetadataExtension(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );

        emit BridgeTypes.LogMetadata(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-490)
```text
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
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

        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        IERC1155(tokenAddress).safeTransferFrom(
            msg.sender,
            address(this),
            tokenId,
            amount,
            ""
        );

        uint256 extensionValue = msg.value - nativeFee;

        initTransferExtension(
            msg.sender,
            deterministicToken,
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
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L522-535)
```text
    function onERC1155Received(
        address operator,
        address,
        uint256,
        uint256,
        bytes calldata
    ) external view override returns (bytes4) {
        // Only accept transfers that were initiated by this contract itself
        if (operator != address(this)) {
            revert ERC1155DirectSendNotAllowed();
        }

        return this.onERC1155Received.selector;
    }
```

**File:** evm/CLAUDE.md (L36-36)
```markdown
- **Event–transfer atomicity**: `InitTransfer` must only be emitted in a code path where tokens have already been burned/locked in the same transaction. If the token transfer reverts or is skipped, the event must not emit — the NEAR side will treat any emitted event as proof that tokens are held
```

**File:** near/omni-bridge/src/lib.rs (L702-750)
```rust
    #[private]
    #[payable]
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

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
    }
```
