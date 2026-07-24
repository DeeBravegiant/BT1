### Title
Fee-on-Transfer Token Accounting Divergence in `initTransfer` Creates Unbacked Cross-Chain Supply — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`initTransfer` in `OmniBridge.sol` uses the caller-supplied `amount` parameter verbatim in the cross-chain message and emitted event, without verifying the actual number of tokens received by the contract. For fee-on-transfer (deflationary) ERC20 tokens, the contract receives fewer tokens than `amount`, but the destination chain credits the full `amount`, creating unbacked wrapped supply and draining the bridge's locked reserves over time.

### Finding Description
In `initTransfer`, when the token is neither a `isBridgeToken` nor has a `customMinters` entry, the bridge executes a plain `safeTransferFrom`:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // requested amount, not verified post-transfer
    );
}
``` [1](#0-0) 

Immediately after, the function emits `InitTransfer` and calls `initTransferExtension` — both using the original caller-supplied `amount`:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // <-- not actual received amount
    ...
);
``` [2](#0-1) 

In `OmniBridgeWormhole`, `initTransferExtension` publishes this same `amount` into the Wormhole message:

```solidity
Borsh.encodeUint128(amount),
``` [3](#0-2) 

On the destination chain, `finTransfer` releases or mints exactly `payload.amount` tokens to the recipient — the inflated figure, not the actual locked amount:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [4](#0-3) 

No balance snapshot (pre/post `balanceOf` diff) is taken anywhere in the deposit path to reconcile the actual received amount.

### Impact Explanation
Every deposit of a fee-on-transfer token inflates the credited cross-chain amount relative to what is actually locked. On round-trip withdrawals, the bridge pays out more than it holds. Repeated deposits drain the bridge's ERC20 reserves, eventually making legitimate withdrawals impossible (frozen redemption path / undercollateralized locked supply). This maps directly to:

- **Critical**: Unauthorized release of locked bridge assets — `finTransfer` releases `amount` while only `amount − fee` was ever locked.
- **Critical**: Irreversible fund lock for later withdrawers once reserves are exhausted.

### Likelihood Explanation
The `initTransfer` function is fully public with no token whitelist. Any caller can pass any ERC20 address. Fee-on-transfer tokens (e.g., tokens with configurable transfer fees, STA, PAXG in some configurations) are real and deployed on mainnet. No privileged access or external compromise is required — a single unprivileged user calling `initTransfer` with such a token triggers the accounting divergence.

### Recommendation
Record the actual received balance using a pre/post `balanceOf` snapshot and use the delta as the credited amount:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived > 0, "zero received");
// use actualReceived instead of amount for the cross-chain message
```

Alternatively, maintain an explicit allowlist of accepted ERC20 tokens and reject any token whose transfer semantics are not standard.

### Proof of Concept
1. A fee-on-transfer ERC20 token `FeeToken` charges 5% on every transfer.
2. Alice calls `initTransfer(FeeToken, 1000, 0, 0, "alice.near", "")`.
3. `safeTransferFrom` moves 1000 tokens from Alice, but the contract receives only 950 (50 taken as fee).
4. `InitTransfer` event and Wormhole message are published with `amount = 1000`.
5. On NEAR (or another destination), the bridge credits Alice with 1000 wrapped tokens.
6. Alice calls the destination bridge to transfer 1000 tokens back to Ethereum.
7. `finTransfer` on Ethereum calls `safeTransfer(Alice, 1000)`, releasing 1000 tokens from reserves that only hold 950.
8. After ~20 such round-trips, the bridge's `FeeToken` reserve is fully drained; all subsequent legitimate withdrawers are permanently locked out.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-354)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L136-137)
```text
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
```
