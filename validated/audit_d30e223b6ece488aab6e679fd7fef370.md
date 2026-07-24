### Title
Fee-on-Transfer Token Locks Less Than Emitted Amount, Creating Unbacked Wrapped Supply on Destination Chain - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary

`OmniBridge.initTransfer` uses the caller-supplied `amount` parameter in the `InitTransfer` event and in the cross-chain message, rather than the actual token balance delta received by the contract. For fee-on-transfer (deflationary) ERC20 tokens, the bridge locks fewer tokens than it advertises, causing the destination chain to mint more wrapped tokens than are backed by real collateral.

### Finding Description

In `OmniBridge.initTransfer`, the plain ERC20 lock path (the `else` branch for tokens that are neither `isBridgeToken` nor `customMinters`) executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // ← caller-supplied parameter
);
``` [1](#0-0) 

Immediately after, the function emits the cross-chain event using the same caller-supplied `amount`, not the actual balance change:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,          // ← same caller-supplied parameter, not actual received
    fee,
    nativeFee,
    recipient,
    message
);
``` [2](#0-1) 

The `InitTransfer` event definition confirms `amount` is the field consumed by the destination chain: [3](#0-2) 

In the Wormhole variant (`OmniBridgeWormhole`), `initTransferExtension` receives the same `amount` parameter and encodes it verbatim into the Wormhole message published to the NEAR side: [4](#0-3) 

No balance snapshot is taken before or after the `safeTransferFrom` call to compute the actual received amount. The contract never checks `balanceOf(address(this))` before and after the transfer.

### Impact Explanation

For a fee-on-transfer token with a `k%` fee, a user calling `initTransfer(token, amount, ...)` causes:
- **Locked on EVM:** `amount * (1 - k/100)` tokens
- **Minted on NEAR:** `amount` wrapped tokens

The bridge's 1:1 backing guarantee is broken. Each such call inflates the wrapped supply on the destination chain beyond the actual locked collateral. Repeated calls drain the bridge's real reserves. When later users attempt to bridge back (triggering `finTransfer` on EVM, which calls `IERC20.safeTransfer(recipient, payload.amount)`), the bridge will eventually be unable to fulfill redemptions — the last redeemers receive nothing, constituting an irreversible fund lock for them.

This falls under **High** impact: balance-accounting divergence that breaks backing guarantees and sends value to the wrong party (unbacked minted tokens on NEAR).

### Likelihood Explanation

- `initTransfer` is a fully public, permissionless function — any unprivileged user can call it.
- The bridge accepts arbitrary ERC20 token addresses; there is no whitelist or fee-on-transfer check.
- Fee-on-transfer tokens are a well-known, deployed token class (e.g., tokens with redistribution mechanics, PAXG, historical USDT fee mode, STA, etc.).
- The attacker does not need any special role, leaked key, or external dependency compromise.

### Recommendation

Measure the actual balance delta received by the contract and use that as the canonical `amount` for the cross-chain message:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 balanceAfter = IERC20(tokenAddress).balanceOf(address(this));
    uint128 actualReceived = uint128(balanceAfter - balanceBefore);
    // use actualReceived instead of amount for the event and cross-chain message
}
```

Alternatively, explicitly document and enforce that only non-fee-on-transfer tokens are supported, and add a check (e.g., require `balanceAfter - balanceBefore == amount`) that causes the transaction to revert for deflationary tokens rather than silently creating unbacked supply.

### Proof of Concept

1. Deploy or use an existing fee-on-transfer ERC20 token `FeeToken` with a 5% transfer fee. Register it with the bridge (it is neither `isBridgeToken` nor has a `customMinters` entry).
2. Call `OmniBridge.initTransfer(address(FeeToken), 1000e18, 0, nativeFee, "near:attacker.near", "")`.
3. Inside `initTransfer`, `safeTransferFrom(msg.sender, address(this), 1000e18)` executes. Due to the 5% fee, only `950e18` tokens arrive at the bridge contract.
4. The function emits `InitTransfer(..., amount=1000e18, ...)` — the full caller-supplied value.
5. The NEAR side (or Wormhole relayer) reads `amount = 1000e18` from the event/message and mints `1000e18` wrapped tokens to `attacker.near`.
6. The bridge holds only `950e18` real tokens but has issued `1000e18` wrapped tokens — `50e18` tokens of unbacked supply exist.
7. Repeating this attack gradually depletes the bridge's real reserves. Eventually, legitimate users bridging back cannot redeem their wrapped tokens because the bridge's ERC20 balance is insufficient to cover `payload.amount` in `finTransfer`. [5](#0-4) [2](#0-1)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-412)
```text
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L427-436)
```text
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

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L23-32)
```text
    event InitTransfer(
        address indexed sender,
        address indexed tokenAddress,
        uint64 indexed originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string recipient,
        string message
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
