### Title
Non-Deterministic `CREATE` Deployment of Bridge Token Proxies Enables Reorg-Based Asset Loss - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.deployToken` deploys each wrapped bridge token proxy using the Solidity `new` keyword, which compiles to the `CREATE` opcode. The resulting address is a function of the deploying contract's address and its current nonce. On any L2 chain where the OmniBridge is deployed, a block reorganisation causes the nonce at the time of re-execution to differ, producing a different proxy address. Users who received wrapped tokens at the pre-reorg address can no longer redeem them through the bridge, because the bridge's canonical mapping now points to the post-reorg address.

---

### Finding Description

`deployToken` is a public, permissionless entry point (it only requires a valid ECDSA signature that any relayer can supply):

```solidity
// OmniBridge.sol L162-172
address bridgeTokenProxy = address(
    new ERC1967Proxy(
        tokenImplementationAddress,
        abi.encodeWithSelector(
            BridgeToken.initialize.selector,
            metadata.name,
            metadata.symbol,
            decimals
        )
    )
);
``` [1](#0-0) 

The deployed address is immediately written into the canonical token mappings:

```solidity
isBridgeToken[address(bridgeTokenProxy)] = true;
ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
nearToEthToken[metadata.token] = address(bridgeTokenProxy);
``` [2](#0-1) 

If a reorg reorganises the block containing `deployToken`, the OmniBridge contract's nonce at re-execution will differ (because other transactions may have been included or excluded), so `CREATE` produces address **B** instead of the original address **A**. All three mappings are now keyed on **B**. Any `finTransfer` calls that were also included in the reorged blocks and minted tokens to users at address **A** are rolled back, but any off-chain state (approvals, DeFi deposits, cross-chain messages referencing **A**) is permanently invalidated. More critically, if a user received tokens at **A** in a block that survived the reorg (i.e., was in a fork that the user's wallet observed), those tokens exist at an address the bridge no longer recognises as a bridge token, making them permanently unburnable/unredeemable.

No privileged role is required to trigger `deployToken`; any party holding a valid bridge signature (i.e., any relayer) can call it. [3](#0-2) 

---

### Impact Explanation

After a reorg, `nearToEthToken[metadata.token]` points to the new address **B**. Tokens that were minted at address **A** (ERC-20 state on the pre-reorg chain tip) are stranded: `isBridgeToken[A]` is `false` in the post-reorg state, so `initTransfer` will not call `BridgeToken(A).burn`, and `finTransfer` will fall through to a plain `safeTransfer` rather than a mint, breaking the backing guarantee. This maps to:

- **High** — Asset-identity and token-mapping divergence that breaks backing guarantees and sends value to the wrong party.
- **Critical** — Irreversible fund lock / permanently unclaimable user value if tokens at address **A** cannot be redeemed.

---

### Likelihood Explanation

L2 chains (Arbitrum, Optimism, Base, Polygon) are the primary deployment targets for EVM bridges and are known to experience sequencer-level reorgs. The `deployToken` function is callable by any relayer with a valid signature, making the triggering transaction routine protocol traffic rather than an adversarial action. The reorg itself need not be attacker-induced; natural sequencer reorgs suffice.

---

### Recommendation

Replace the `new ERC1967Proxy(...)` call with a `CREATE2`-based deployment using a salt derived from the canonical NEAR token identifier (`metadata.token`), which is already unique per token and known before deployment:

```solidity
bytes32 salt = keccak256(abi.encodePacked(metadata.token));
address bridgeTokenProxy = address(
    new ERC1967Proxy{salt: salt}(
        tokenImplementationAddress,
        abi.encodeWithSelector(
            BridgeToken.initialize.selector,
            metadata.name,
            metadata.symbol,
            decimals
        )
    )
);
```

This makes the proxy address fully deterministic and reorg-stable: the same NEAR token ID always maps to the same EVM address regardless of nonce or block ordering.

---

### Proof of Concept

1. Relayer submits `deployToken(sig, metadata)` for NEAR token `"usdc.near"`. OmniBridge nonce = 5. `CREATE` deploys proxy at address **A**. `nearToEthToken["usdc.near"] = A`.
2. A user calls `finTransfer` in the same or next block; tokens are minted at **A** to the user.
3. L2 sequencer reorg occurs. Both transactions are re-sequenced, but an earlier transaction in the same block was dropped, so OmniBridge nonce = 4 at re-execution. `CREATE` now deploys proxy at address **B** ≠ **A**. `nearToEthToken["usdc.near"] = B`.
4. The user's ERC-20 balance at **A** is rolled back (it was in the reorged block). However, if the user had already observed the balance and bridged **A** tokens into an external protocol (e.g., via a same-block atomic call or a surviving side-chain transaction), those tokens are at an address the bridge no longer recognises.
5. `isBridgeToken[A]` is `false`. Any future attempt to call `initTransfer(A, ...)` will attempt `safeTransfer` from the user rather than `burn`, and will revert or silently lock funds, permanently breaking redemption for address **A**. [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-155)
```text
    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
        if (tokenImplementationAddress == address(0)) {
            revert TokenImplementationNotSet();
        }
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

        require(
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L162-172)
```text
        address bridgeTokenProxy = address(
            new ERC1967Proxy(
                tokenImplementationAddress,
                abi.encodeWithSelector(
                    BridgeToken.initialize.selector,
                    metadata.name,
                    metadata.symbol,
                    decimals
                )
            )
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L190-192)
```text
        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L404-412)
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
```
