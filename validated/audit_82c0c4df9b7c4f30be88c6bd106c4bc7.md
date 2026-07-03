### Title
Weak UID Generation Allows Same-Block Collision, Permanently Freezing Unclaimed Yield — (`contracts/bridges/SonicChainNativeTokenBridge.sol`)

---

### Summary

`bridgeTokenToL1` generates a `uint96` UID from fields that are entirely identical across two transactions submitted in the same block with the same `msg.sender`, `recipient`, `amount`, and `tx.gasprice`. Because no per-transaction unique value (nonce, `tx.origin` nonce, or `blockhash`) is included, a collision is deterministically producible. Both calls succeed on-chain, both burn/transfer tokens into the Sonic bridge, but only one withdrawal record can exist under that UID on L1, permanently freezing the second batch of yield tokens.

---

### Finding Description

The UID is computed as:

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

Every input to this hash is **block-level or call-parameter-level**, not transaction-level:

| Field | Unique per tx? |
|---|---|
| `block.timestamp` | No — same for all txs in a block |
| `block.number` | No — same for all txs in a block |
| `msg.sender` | No — same if same EOA/contract |
| `recipient` | No — caller-controlled |
| `amount` | No — caller-controlled |
| `tx.gasprice` | No — same if EIP-1559 effective price matches |
| `bridgeReceiver` | No — immutable constant |

No transaction nonce, no `tx.origin` nonce, no `blockhash`, no counter storage variable is included. Two transactions from the same sender in the same block with identical parameters produce **bit-for-bit the same UID**.

The `nonReentrant` guard prevents reentrancy within a single call but does **not** prevent two separate transactions from landing in the same block. [2](#0-1) 

Both calls then invoke:

```solidity
sonicBridge.withdraw(uid, originalToken, amount);
``` [3](#0-2) 

The `ISonicBridge` interface documents `uid` as a **"Unique identifier for the withdrawal"**, meaning the bridge is designed to key withdrawal records on this value. [4](#0-3) 

If the Sonic bridge stores withdrawals in a `uid → record` mapping (the standard design for such bridges), the second `withdraw` call either:
- **Overwrites** the first record → first batch is unclaimable, or
- **Silently no-ops** → second batch has no record, unclaimable.

Either way, one batch of tokens is burned on Sonic with no corresponding claimable record on L1.

On L1, `SonicBridgeReceiver.claimAndTransferToVault` tracks claimed IDs:

```solidity
mapping(uint256 withdrawalId => bool claimed) public claimedWithdrawals;
``` [5](#0-4) 

Only one claim per UID is satisfiable. The second batch of yield tokens is permanently frozen with no recovery path (the `emergencyRecover` function on `SonicBridgeReceiver` only recovers tokens already held by the contract on L1, not tokens stuck in the bridge's withdrawal queue). [6](#0-5) 

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Yield tokens bridged in the colliding transaction are burned on Sonic and have no claimable L1 record. They cannot be recovered through any permissionless path. The `recoverTokens` function on the Sonic-side bridge only recovers tokens held by the bridge contract itself, not tokens already consumed by `sonicBridge.withdraw`. [7](#0-6) 

---

### Likelihood Explanation

**Low-to-Medium.** The collision requires the same sender, same amount, same gas price, and same block. This is unlikely for a human user but is realistic for:

- Automated bridging bots or keeper systems (e.g., `RSETHPoolV3AutoBridgedTokens`) that retry or batch identical bridge calls
- A deliberate griefing actor who front-runs or mirrors an in-flight transaction with identical parameters in the same block

The `% type(uint96).max` modulo (not `% (type(uint96).max + 1)`) also means the value `2^96 - 1` is unreachable, slightly reducing the UID space, though this is a minor secondary issue. [8](#0-7) 

---

### Recommendation

Replace the entropy-poor hash with a contract-managed monotonic counter:

```solidity
uint96 private _uidCounter;

function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
    ...
    unchecked { _uidCounter++; }
    uint96 uid = _uidCounter;
    ...
}
```

This guarantees uniqueness regardless of block, sender, amount, or gas price. Alternatively, include `block.prevrandao` and a storage nonce keyed on `msg.sender` to make collisions computationally infeasible.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

contract MockSonicBridge {
    mapping(uint96 => uint256) public withdrawals; // uid => amount
    uint96 public lastUid;

    function withdraw(uint96 uid, address, uint256 amount) external {
        // Silently overwrites — standard mapping behavior
        withdrawals[uid] = amount;
        lastUid = uid;
    }
}

// Test (Foundry):
// 1. Deploy MockSonicBridge, MockTokenPairs, MockToken
// 2. Deploy SonicChainNativeTokenBridge with MockSonicBridge
// 3. In a single test (same block.number, block.timestamp):
//    - Call bridgeTokenToL1(recipient, 1e18) → uid_1
//    - Call bridgeTokenToL1(recipient, 1e18) → uid_2
// 4. Assert uid_1 == uid_2 (collision)
// 5. Assert MockSonicBridge.withdrawals[uid_1] == 1e18 (only last write survives)
// 6. Assert total tokens burned == 2e18 but only 1e18 is claimable on L1
// => Second 1e18 of yield tokens is permanently frozen
```

The collision is deterministic: with identical `block.timestamp`, `block.number`, `msg.sender`, `recipient`, `amount`, `tx.gasprice`, and `bridgeReceiver`, `keccak256(abi.encodePacked(...))` returns the same hash, producing the same UID. [9](#0-8)

### Citations

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L73-73)
```text
    function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L92-105)
```text
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
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L115-115)
```text
        sonicBridge.withdraw(uid, originalToken, amount);
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L160-173)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert InvalidAmount();

        IERC20(tokenAddress).safeTransfer(recipient, amount);
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

**File:** contracts/bridges/SonicBridgeReceiver.sol (L30-30)
```text
    mapping(uint256 withdrawalId => bool claimed) public claimedWithdrawals;
```

**File:** contracts/bridges/SonicBridgeReceiver.sol (L164-172)
```text
    function emergencyRecover(address token, address recipient, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(recipient);

        uint256 balance = IERC20(token).balanceOf(address(this));
        uint256 recoverAmount = amount == 0 ? balance : amount;
        if (recoverAmount > balance) revert InsufficientBalance();

        IERC20(token).safeTransfer(recipient, recoverAmount);
    }
```
