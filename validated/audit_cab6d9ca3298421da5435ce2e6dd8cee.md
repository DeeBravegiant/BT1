### Title
Fee-on-Transfer Token Accounting Divergence in `initTransfer` Creates Unbacked Supply on NEAR — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` assumes that `safeTransferFrom(msg.sender, address(this), amount)` always delivers exactly `amount` tokens to the bridge. For fee-on-transfer ERC20 tokens the bridge receives `amount − fee_deducted`, yet it emits `amount` in the `InitTransfer` event. The NEAR side trusts that event value and mints the full `amount` to the recipient, permanently inflating supply beyond what is actually locked on EVM. The same flaw exists in Starknet's `init_transfer`.

---

### Finding Description

**EVM path** — `OmniBridge.sol` `initTransfer`, non-bridge-token branch:

```solidity
// line 407-411
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // ← requested amount, not verified against actual receipt
);
```

Immediately after, the function emits:

```solidity
// line 427-436
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // ← same caller-supplied value, not actual balance delta
    fee,
    nativeFee,
    recipient,
    message
);
```

No balance-before / balance-after check is performed. For a fee-on-transfer token the bridge holds `amount − transfer_fee` but the event asserts `amount`. The NEAR bridge reads this event via its prover and mints the full `amount` to the recipient.

**Starknet path** — `starknet/src/omni_bridge.cairo` `init_transfer`, non-bridge-token branch:

```cairo
// lines 304-306
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
assert(success, 'ERR_TRANSFER_FROM_FAILED');
```

The emitted `InitTransfer` event (lines 316-330) carries the original `amount`, not the actual received amount. Identical root cause.

**Why the attack path is fully unprivileged:**

`logMetadata` (line 224) carries **no access control**:

```solidity
function logMetadata(address tokenAddress) external payable {
    ...
    emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
}
```

Any user can call it for any ERC20, including a self-deployed fee-on-transfer token. The relayer picks up the `LogMetadata` event and calls `deploy_token` on NEAR, registering the token. Once registered, `initTransfer` on EVM will lock the token (receiving less than `amount`) while NEAR mints the full `amount`.

---

### Impact Explanation

Each `initTransfer` call with a fee-on-transfer token creates a shortfall: the EVM bridge holds `amount − fee` but NEAR has minted `amount`. Repeating this inflates NEAR-side supply without backing. When legitimate holders of the NEAR-side token attempt to redeem through `finTransfer` on EVM, the bridge's locked balance is insufficient to cover all redemptions — permanently locking or losing user funds. This is a direct backing-guarantee violation.

Fits: **High — balance-accounting divergence that breaks backing guarantees.**

---

### Likelihood Explanation

- `logMetadata` is permissionless; any attacker can register an arbitrary ERC20.
- The attacker controls the token contract and can set any transfer-fee rate.
- No admin approval is required at any step.
- The NEAR relayer is designed to process all `LogMetadata` events automatically.
- The only cost to the attacker is the transfer fee they themselves programmed into the token.

---

### Recommendation

Measure the actual balance delta after `safeTransferFrom` and use that value in the emitted event:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint128 actualReceived = uint128(
    IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore
);
// replace `amount` with `actualReceived` in initTransferExtension and the event
```

Apply the same fix to Starknet's `init_transfer`. Alternatively, document and enforce that only tokens without transfer fees may be registered (e.g., validate in `logMetadata` or on the NEAR `deploy_token` path).

---

### Proof of Concept

1. Attacker deploys `FeeToken`, an ERC20 that silently deducts 5% on every `transferFrom`.
2. Attacker calls `OmniBridge.logMetadata(FeeToken)` — succeeds with no role check.
3. Relayer observes `LogMetadata`, calls `deploy_token` on NEAR; NEAR registers `FeeToken`.
4. Attacker calls `OmniBridge.initTransfer(FeeToken, 1000, 0, 0, "near:attacker.near", "")`.
5. `safeTransferFrom` moves 1000 tokens; `FeeToken` deducts 50 → bridge receives **950**.
6. `InitTransfer` event emits `amount = 1000`.
7. NEAR prover verifies the event; NEAR bridge mints **1000** wrapped `FeeToken` to `attacker.near`.
8. Net gain: 50 unbacked tokens per iteration. Repeating drains the bridge's real reserve.

**Relevant lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-232)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
    }
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

**File:** starknet/src/omni_bridge.cairo (L303-330)
```text
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
