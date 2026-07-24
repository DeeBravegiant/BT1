### Title
Unregistered ERC20 tokens accepted by `initTransfer` cause permanent fund lock with no recovery path — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge.initTransfer` accepts any arbitrary ERC20 token via its unguarded `else` branch without verifying the token is registered in the bridge. When an unregistered token is deposited, the NEAR-side finalization panics on a missing decimals lookup, leaving the tokens permanently locked in the EVM bridge contract with no on-chain rescue mechanism.

---

### Finding Description

`initTransfer` routes ERC20 handling through three guarded branches and one unguarded fallthrough:

```
if (customMinters[tokenAddress] != address(0)) { … burn via custom minter … }
else if (isBridgeToken[tokenAddress])           { … burn bridge token … }
else {
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
}
```

The `else` branch is intended for EVM-native tokens that are locked on the EVM side while a wrapped representation is minted on NEAR. However, there is **no check** that `ethToNearToken[tokenAddress]` is non-empty (i.e., that the token has been registered via `deployToken` or `addCustomToken`). Any caller can pass an arbitrary ERC20 address, and the contract will silently accept the transfer.

On the NEAR side, `fin_transfer_callback` immediately calls:

```rust
let decimals = self
    .token_decimals
    .get(&init_transfer.token)
    .near_expect(BridgeError::TokenDecimalsNotFound);
```

For an unregistered token, `token_decimals` has no entry, so this panics and the cross-chain finalization is permanently rejected. The EVM-side tokens remain locked in `OmniBridge` with no corresponding NEAR-side credit ever issued.

`OmniBridge.sol` contains no `rescueTokens`, `withdrawERC20`, or equivalent admin function. The only recovery path would be a UUPS upgrade to add such a function — an out-of-band privileged action that is not guaranteed and leaves users with no self-service remedy.

---

### Impact Explanation

**Critical — Irreversible fund lock.**

Any ERC20 token deposited via `initTransfer` with an unregistered `tokenAddress` is permanently unclaimable:
- The EVM bridge holds the tokens with no release path.
- The NEAR bridge rejects the finalization (`TokenDecimalsNotFound`).
- No on-chain rescue function exists in `OmniBridge.sol`.

---

### Likelihood Explanation

**Medium.** Any unprivileged user can call `initTransfer` with an unregistered token address. This can happen accidentally (user bridges a token before `deployToken` is called) or deliberately (griefing another user's tokens). The public API provides no warning or revert to prevent it.

---

### Recommendation

Add a registration guard at the top of the ERC20 branch in `initTransfer`:

```solidity
} else {
+   require(bytes(ethToNearToken[tokenAddress]).length != 0, "ERR_TOKEN_NOT_REGISTERED");
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
}
```

Additionally, add an admin-only ERC20 rescue function as a safety net for any tokens that may already be stuck:

```solidity
function rescueERC20(address token, address to, uint256 amount)
    external onlyRole(DEFAULT_ADMIN_ROLE)
{
    IERC20(token).safeTransfer(to, amount);
}
```

---

### Proof of Concept

1. Alice holds 1000 USDC. USDC has not yet been registered via `deployToken` (i.e., `ethToNearToken[USDC] == ""`).
2. Alice calls `initTransfer(USDC, 1000e6, 0, 0, "alice.near", "")`.
3. The `else` branch executes: `USDC.safeTransferFrom(Alice, OmniBridge, 1000e6)` — succeeds. [1](#0-0) 
4. `InitTransfer` event is emitted. Relayer picks it up and calls `fin_transfer` on NEAR.
5. NEAR's `fin_transfer_callback` calls `self.token_decimals.get(&init_transfer.token).near_expect(BridgeError::TokenDecimalsNotFound)` — panics. [2](#0-1) 
6. NEAR finalization is permanently rejected. Alice's 1000 USDC remain locked in `OmniBridge` forever.
7. There is no `rescueTokens` or equivalent function in `OmniBridge.sol` to recover them. [3](#0-2)

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

**File:** near/omni-bridge/src/lib.rs (L719-722)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```
