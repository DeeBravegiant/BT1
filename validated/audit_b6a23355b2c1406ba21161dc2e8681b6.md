### Title
Rebasable Token Negative Rebase Causes Permanent Fund Lock for Last Withdrawers — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`OmniBridge.initTransfer` locks a caller-supplied `amount` of any ERC-20 token and emits that same `amount` as the canonical bridged value. For rebasable tokens (e.g., AMPL), the bridge's actual custody balance can silently decrease after a negative rebase. Because `finTransfer` unconditionally releases `payload.amount` tokens from the bridge's balance, the last users to withdraw will find the contract insolvent and their funds permanently unclaimable.

### Finding Description

**Lock path — `initTransfer`**

When a non-bridge, non-custom-minter ERC-20 is deposited, the bridge executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // caller-supplied, not verified against actual received balance
);
``` [1](#0-0) 

Immediately after, the same caller-supplied `amount` is broadcast cross-chain (via Wormhole message or MPC-signed payload) and emitted in the `InitTransfer` event:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message
);
``` [2](#0-1) 

The Wormhole variant encodes this same `amount` verbatim into the cross-chain message:

```solidity
Borsh.encodeUint128(amount),
``` [3](#0-2) 

NEAR receives this message and mints exactly `amount` wrapped tokens to the user. No balance-before/balance-after check is performed anywhere to confirm what the bridge actually received.

**Unlock path — `finTransfer`**

When a user bridges back, the bridge releases exactly `payload.amount` from its custody:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [4](#0-3) 

`payload.amount` is the value that was signed by the MPC/NEAR bridge at lock time — it is never recalculated against the bridge's current actual balance.

**Rebase divergence**

For a rebasable token, the token contract adjusts every holder's balance globally. After a negative rebase of factor `r` (0 < r < 1):

- Bridge's actual token balance: `Σ(locked_i) × r`
- Sum of all signed withdrawal amounts: `Σ(locked_i)` (unchanged)

The first `1/r` fraction of withdrawers succeed; the remainder revert because `safeTransfer` will fail when the bridge balance is exhausted. Those users hold valid MPC-signed `finTransfer` payloads that can never be executed — their funds are permanently locked.

### Impact Explanation

**Critical — Irreversible fund lock / permanently unclaimable user value in bridge flows.**

After a negative rebase, a portion of users holding valid, MPC-signed withdrawal authorizations cannot redeem their tokens. The bridge contract has no mechanism to recalculate or pro-rate withdrawals against its actual balance. The shortfall is permanent unless an admin manually re-supplies tokens, which is outside the protocol's normal operation.

### Likelihood Explanation

**Medium.** Rebasable tokens (AMPL, stETH rebasing variants, etc.) are widely deployed on EVM chains. The `initTransfer` path imposes no allowlist — any ERC-20 that has been registered via `logMetadata`/`deployToken` can be bridged. A negative rebase (or slashing event) is an ordinary, protocol-defined operation of these tokens, not an attack in itself. No privileged access is required; any user who bridges a rebasable token and experiences a rebase while their tokens are in custody is affected.

### Recommendation

1. **Balance-before / balance-after check in `initTransfer`:** Record `IERC20(tokenAddress).balanceOf(address(this))` before and after `safeTransferFrom`; use the delta as the canonical bridged amount rather than the caller-supplied `amount`.

2. **Pro-rata withdrawal accounting:** Maintain a per-token `totalLocked` counter and, at `finTransfer` time, release `min(payload.amount, actualBalance × payload.amount / totalLocked)` to prevent insolvency.

3. **Token allowlist with rebase flag:** Explicitly flag or block rebasable tokens at registration time so operators can make an informed decision before enabling bridging.

### Proof of Concept

1. AMPL is registered on Omni Bridge via `logMetadata` + `deployToken`.
2. Alice calls `initTransfer(AMPL, 1_000e18, ...)`. Bridge receives 1 000 AMPL; NEAR mints 1 000 wAMPL to Alice.
3. Bob calls `initTransfer(AMPL, 1_000e18, ...)`. Bridge now holds 2 000 AMPL; NEAR mints 1 000 wAMPL to Bob.
4. A negative rebase of −30 % occurs. Bridge's actual AMPL balance drops to 1 400 AMPL.
5. Alice burns 1 000 wAMPL on NEAR; MPC signs a `finTransfer` payload for `amount = 1 000`.
6. Alice calls `finTransfer` on EVM → succeeds; bridge balance: 400 AMPL.
7. Bob burns 1 000 wAMPL on NEAR; MPC signs a `finTransfer` payload for `amount = 1 000`.
8. Bob calls `finTransfer` on EVM → `safeTransfer` reverts (bridge holds only 400 AMPL). Bob's 600 AMPL equivalent is permanently unclaimable. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-354)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L136-137)
```text
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
```
