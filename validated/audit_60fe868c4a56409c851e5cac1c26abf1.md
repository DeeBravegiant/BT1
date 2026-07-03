### Title
Non-Unique Bridge UID Generation Allows Same-Block Collision, Risking Bridge Withdrawal Loss - (File: contracts/bridges/SonicChainNativeTokenBridge.sol)

---

### Summary

`SonicChainNativeTokenBridge.bridgeTokenToL1` generates a `uint96 uid` used as the unique identifier for each Sonic bridge withdrawal using only block-level and call-parameter data. Two calls with identical parameters in the same block produce the same `uid`, which is then passed to `sonicBridge.withdraw(uid, ...)`. This is the direct analog of the reported hash-collision vulnerability: a unique request identifier is derived from inputs that are not guaranteed to be distinct across multiple requests.

---

### Finding Description

In `contracts/bridges/SonicChainNativeTokenBridge.sol`, the `bridgeTokenToL1` function generates the bridge uid as follows:

```solidity
uint96 uid = uint96(
    uint256(
        keccak256(
            abi.encodePacked(
                block.timestamp, block.number, msg.sender, recipient, amount, tx.gasprice, bridgeReceiver
            )
        )
    ) % type(uint96).max
);
``` [1](#0-0) 

Every input to this hash is fully determined by the block context and the call parameters. If the same `msg.sender` submits two calls to `bridgeTokenToL1` with the same `recipient`, `amount`, and `tx.gasprice` within the same block, all six inputs are identical, producing the same `uid`. The fallback uid path only handles the case where `uid == 0`; it does not handle collisions with prior non-zero uids:

```solidity
if (uid == 0) {
    uid = uint96(uint256(keccak256(abi.encodePacked(block.number, msg.sender, tx.gasprice, bridgeReceiver))));
}
``` [2](#0-1) 

The colliding uid is then passed directly to the Sonic bridge:

```solidity
sonicBridge.withdraw(uid, originalToken, amount);
``` [3](#0-2) 

The Sonic bridge interface shows `uid` is the primary withdrawal identifier: [4](#0-3) 

---

### Impact Explanation

The impact bifurcates on the Sonic bridge's uid-uniqueness enforcement:

- **If the bridge reverts on duplicate uid**: The second transaction reverts entirely (including the `safeTransferFrom`), so the user's tokens are returned. However, the user cannot bridge the same amount to the same recipient in the same block — a temporary denial of service for that specific parameter set.
- **If the bridge silently accepts or overwrites on duplicate uid**: The first withdrawal registration is overwritten. The tokens transferred in the first call have already been approved and consumed by the bridge, but the bridge now only tracks the second call's withdrawal. The first user's bridged tokens are permanently lost — a critical fund loss.

The `BridgeFailed` check only verifies that the bridge consumed the tokens in the *current* call; it cannot detect that a prior call's withdrawal was overwritten: [5](#0-4) 

---

### Likelihood Explanation

**Low.** The collision requires the same `msg.sender`, `recipient`, `amount`, and `tx.gasprice` in the same block. This can occur naturally if a user's wallet submits a duplicate transaction (e.g., due to a nonce-reuse bug, a retry, or a mempool resubmission), or if a smart contract caller invokes `bridgeTokenToL1` twice in the same transaction batch. On a high-throughput chain like Sonic, same-block inclusion of duplicate transactions is more likely than on Ethereum mainnet.

---

### Recommendation

Replace the block-data-derived uid with a globally incrementing counter, guaranteeing uniqueness regardless of call parameters or block context:

```solidity
uint256 public bridgeNonce;

// Inside bridgeTokenToL1:
uint96 uid = uint96(++bridgeNonce);
```

Alternatively, include the nonce in the hash to preserve the obfuscated uid format while ensuring uniqueness:

```solidity
uint96 uid = uint96(
    uint256(
        keccak256(
            abi.encodePacked(
                block.timestamp, block.number, msg.sender, recipient, amount, tx.gasprice, bridgeReceiver, ++bridgeNonce
            )
        )
    ) % type(uint96).max
);
```

---

### Proof of Concept

1. User (or a smart contract) calls `bridgeTokenToL1(recipient, 1e18)` twice in the same block with the same `tx.gasprice`.
2. Both calls compute `uid = keccak256(block.timestamp || block.number || msg.sender || recipient || 1e18 || tx.gasprice || bridgeReceiver) % type(uint96).max` — identical values.
3. First call: tokens transferred from user → contract, bridge approves, `sonicBridge.withdraw(uid, token, 1e18)` registers withdrawal #uid.
4. Second call: tokens transferred from user → contract, bridge approves, `sonicBridge.withdraw(uid, token, 1e18)` is called with the same uid.
5. If the Sonic bridge overwrites: withdrawal #uid now points to the second call; the first call's tokens are consumed by the bridge but the corresponding L1 claim is lost — permanent fund loss for the first call's amount.
6. If the Sonic bridge reverts: the second transaction reverts entirely (tokens returned), but the user is blocked from bridging the same parameters in the same block. [6](#0-5)

### Citations

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L73-124)
```text
    function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
        UtilLib.checkNonZeroAddress(recipient);

        // recipient parameter is kept for IL2TokenBridge interface compatibility
        // but the actual flow will be: SonicBridge -> SonicBridgeReceiver -> L1Vault
        if (amount == 0) revert InvalidAmount();

        // No additional msg.value is needed for the fees
        if (msg.value != 0) revert NoMsgValueNeeded();

        token.safeTransferFrom(msg.sender, address(this), amount);

        uint256 balance = token.balanceOf(address(this));
        if (balance < amount) revert InsufficientBalance();

        // Get the original token address (validated in constructor)
        address originalToken = tokenPairs.mintedToOriginal(address(token));

        // Generate a unique UID for this transaction
        uint96 uid = uint96(
            uint256(
                keccak256(
                    abi.encodePacked(
                        block.timestamp, block.number, msg.sender, recipient, amount, tx.gasprice, bridgeReceiver
                    )
                )
            ) % type(uint96).max
        );

        // Ensure UID is not zero
        if (uid == 0) {
            uid = uint96(uint256(keccak256(abi.encodePacked(block.number, msg.sender, tx.gasprice, bridgeReceiver))));
        }

        // Store the current token balance before withdrawal
        uint256 balanceBefore = token.balanceOf(address(this));

        // Approve the Sonic bridge to spend the tokens
        token.safeIncreaseAllowance(address(sonicBridge), amount);

        // Initiate withdrawal on Sonic bridge
        // Note: Sonic gateway will only allow SonicBridgeReceiver to claim (same address as this contract)
        sonicBridge.withdraw(uid, originalToken, amount);

        // Verify tokens were transferred/burned
        uint256 balanceAfter = token.balanceOf(address(this));
        if (balanceBefore - balanceAfter != amount) {
            revert BridgeFailed();
        }

        emit TokensBridgedToL1(recipient, amount, uid, bridgeReceiver);
    }
```

**File:** contracts/interfaces/L2/ISonicBridge.sol (L10-14)
```text
    /// @notice Initiates a withdrawal from Sonic to Ethereum
    /// @param uid Unique identifier for the withdrawal
    /// @param token The original token address on Ethereum
    /// @param amount The amount to withdraw
    function withdraw(uint96 uid, address token, uint256 amount) external;
```
