### Title
Reentrancy in `initTransfer1155` via Malicious ERC1155 Token Enables Unbacked Wrapped-Token Minting on NEAR — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary
`OmniBridge.initTransfer1155` contains no reentrancy guard. It calls `IERC1155.safeTransferFrom` mid-function, after incrementing `currentOriginNonce` but before emitting `InitTransfer`. A fully unprivileged attacker who deploys a malicious ERC1155 token can exploit this to emit multiple distinct `InitTransfer` events — each with a unique, valid nonce — without depositing any real tokens. The NEAR bridge processes every such event as a legitimate lock, minting unbacked wrapped tokens for the attacker.

---

### Finding Description

`initTransfer1155` executes in this order:

1. `currentOriginNonce += 1` — nonce N is reserved.
2. `IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "")` — external call to an **attacker-controlled** contract.
3. `initTransferExtension(...)` — Wormhole/other relay hook.
4. `emit BridgeTypes.InitTransfer(...)` — event with nonce N. [1](#0-0) 

There is no `ReentrancyGuard` or `nonReentrant` modifier anywhere in the inheritance chain (`UUPSUpgradeable`, `AccessControlUpgradeable`, `SelectivePausableUpgradable`, `IERC1155Receiver`). [2](#0-1) 

The `onERC1155Received` hook on the bridge is a `view` function that only checks `operator == address(this)`. Because the bridge itself is the caller of `safeTransferFrom`, the operator check passes — it does **not** block reentrancy. [3](#0-2) 

A malicious ERC1155 token can implement `safeTransferFrom` to:
- Re-enter `initTransfer1155` before returning (nonce N+1 is now reserved, a second `InitTransfer` event will be emitted).
- Never actually transfer any tokens to the bridge.
- Return a success value so the outer call also completes and emits its `InitTransfer` event (nonce N).

Result: two (or more) `InitTransfer` events with distinct nonces N and N+1 are emitted by the canonical `OmniBridge` contract, with zero tokens locked.

`logMetadata1155` is fully permissionless, so the attacker can register the malicious token on NEAR before the attack: [4](#0-3) 

The NEAR `fin_transfer_callback` accepts any event whose `emitter_address` matches a registered factory (the `OmniBridge` address) and whose token has registered decimals. Both conditions are satisfied after the attacker calls `logMetadata1155`. [5](#0-4) 

---

### Impact Explanation

Each `InitTransfer` event emitted by `OmniBridge` is treated by the NEAR bridge as proof that `amount` tokens were locked on EVM. Because every reentrant call increments `currentOriginNonce` and produces a unique nonce, the NEAR-side replay guard (`origin_nonce` uniqueness) does not block any of the events. The NEAR bridge mints wrapped tokens for each event. The attacker receives N × `amount` wrapped tokens while depositing zero real ERC1155 tokens — a direct, irreversible creation of unbacked supply. This falls squarely under **Critical: unauthorized creation of wrapped bridge assets through verification failure**.

---

### Likelihood Explanation

Every step is permissionless and requires no privileged access:
- Deploying a malicious ERC1155 contract: open to any EOA.
- Calling `logMetadata1155` to register the token on NEAR: open to any EOA.
- Calling `initTransfer1155` with the malicious token: open to any EOA.

The only prerequisite is that the NEAR side has processed the `LogMetadata` event (a normal relayer operation). The attack is therefore realistic for any motivated attacker.

---

### Recommendation

1. **Add `Reentranc

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L28-33)
```text
contract OmniBridge is
    UUPSUpgradeable,
    AccessControlUpgradeable,
    SelectivePausableUpgradable,
    IERC1155Receiver
{
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L447-490)
```text
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

**File:** near/omni-bridge/src/lib.rs (L709-722)
```rust
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
