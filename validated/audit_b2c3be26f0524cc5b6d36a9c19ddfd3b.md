### Title
Fee-on-Transfer Token Inflates Bridged Amount, Breaking Backing Guarantee — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`OmniBridge.initTransfer` uses the caller-supplied `amount` parameter verbatim in the cross-chain message after performing a `safeTransferFrom`. For fee-on-transfer ERC20 tokens the contract receives less than `amount`, yet the Wormhole/NEAR message records the full `amount`. The destination chain mints or releases `amount` wrapped tokens backed by only `amount − fee` locked tokens, permanently inflating supply beyond its collateral.

### Finding Description
In `initTransfer`, the native-ERC20 lock path executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
``` [1](#0-0) 

Immediately after, the unmodified caller-supplied `amount` is forwarded to `initTransferExtension`:

```solidity
initTransferExtension(msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message, extensionValue);
``` [2](#0-1) 

`OmniBridgeWormhole.initTransferExtension` then encodes that same `amount` into the Wormhole payload:

```solidity
Borsh.encodeUint128(amount),
``` [3](#0-2) 

No balance snapshot is taken before or after the transfer to verify what was actually received. The `InitTransfer` event also emits the inflated `amount`: [4](#0-3) 

There is no token allowlist enforced in the `else` branch of `initTransfer`; any ERC20 address is accepted, including fee-on-transfer tokens. [5](#0-4) 

### Impact Explanation
The destination chain (NEAR or another EVM) receives a signed message claiming `amount` tokens were locked. It mints or releases `amount` wrapped tokens. The EVM bridge holds only `amount − fee` of the underlying asset. Every such transfer permanently creates unbacked wrapped supply. An attacker can repeat this to drain the destination-chain liquidity pool or accumulate unbacked wrapped tokens redeemable for real assets on other chains, constituting an unauthorized creation of bridge assets and a backing-guarantee break.

This maps directly to the allowed impact: **"Asset-identity, token-mapping, decimals, fee-routing, refund, or balance-accounting divergence that breaks backing guarantees or sends value to the wrong party."**

### Likelihood Explanation
The entry point is fully unprivileged — any address can call `initTransfer` with any ERC20 token address. Fee-on-transfer tokens (e.g., USDT on some chains with fees enabled, STA, PAXG, etc.) exist in production. No admin action is required to trigger the discrepancy; the attacker only needs to call `initTransfer` with such a token that has a corresponding NEAR registration.

### Recommendation
Measure the actual received amount by comparing the contract's balance before and after the `safeTransferFrom`, and use the delta — not the caller-supplied `amount` — in the cross-chain message and event:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint128 actualAmount = uint128(IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore);
// use actualAmount instead of amount going forward
```

Alternatively, maintain an explicit token allowlist for the native-lock path and document that fee-on-transfer tokens are prohibited, enforcing this on-chain.

### Proof of Concept

1. A fee-on-transfer ERC20 token `FEE_TOKEN` (2% fee) is registered on both EVM and NEAR sides of the bridge.
2. Attacker calls `OmniBridge.initTransfer(FEE_TOKEN, 1000e18, 0, 0, "attacker.near", "")`.
3. `safeTransferFrom` moves `1000e18` from attacker; bridge receives `980e18` (2% fee taken by token).
4. `initTransferExtension` encodes `amount = 1000e18` into the Wormhole message.
5. NEAR bridge processes the Wormhole VAA and mints `1000e18` wrapped `FEE_TOKEN` to `attacker.near`.
6. Attacker holds `1000e18` wrapped tokens backed by only `980e18` locked tokens — `20e18` of unbacked supply created per iteration.
7. Repeating inflates unbacked supply until the bridge's locked reserves are insufficient to cover redemptions, causing a shortfall for honest users.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L415-425)
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L136-136)
```text
            Borsh.encodeUint128(amount),
```
