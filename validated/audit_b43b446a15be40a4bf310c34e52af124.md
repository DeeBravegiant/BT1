### Title
Fee-on-Transfer Token Accounting Divergence Breaks Bridge Backing Guarantee — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`OmniBridge.initTransfer` uses the caller-supplied `amount` parameter in the emitted `InitTransfer` event without verifying that exactly `amount` tokens were actually received. For fee-on-transfer ERC-20 tokens, the bridge locks fewer tokens than it credits on the destination chain, permanently breaking the 1:1 backing guarantee and eventually making redemption impossible for some users.

### Finding Description
In `OmniBridge.initTransfer`, the standard ERC-20 lock path is:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol, lines 407-411
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount
);
```

Immediately after, the function emits the cross-chain message using the caller-supplied `amount` — not the actual balance delta received:

```solidity
// lines 427-436
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // <-- caller-controlled, not verified against actual receipt
    fee,
    nativeFee,
    recipient,
    message
);
```

There is no token whitelist for this path. Any ERC-20 that is not a registered bridge token (`isBridgeToken`) and has no custom minter (`customMinters`) falls through to this branch unconditionally. [1](#0-0) 

The NEAR side reads the `amount` field directly from the prover-decoded `InitTransferMessage` and credits it to the recipient: [2](#0-1) [3](#0-2) 

The `fin_transfer_callback` on NEAR denormalizes and stores this `amount` as the canonical transfer amount, which is then used to release or mint tokens to the recipient: [4](#0-3) 

### Impact Explanation
For a fee-on-transfer token with fee rate `f`:
- EVM bridge receives `amount × (1 - f)` tokens but records `amount` in the cross-chain event.
- NEAR credits the user with `amount` tokens (minted or released from NEAR-side reserves).
- The EVM bridge now holds a deficit of `amount × f` per transfer.
- When users bridge back, `finTransfer` calls `safeTransfer(recipient, amount)` but the bridge's actual balance is insufficient for the last redeemers.
- The last users to redeem are permanently locked out — their funds are irretrievably frozen in the bridge.

This matches the **High** impact category: balance-accounting divergence that breaks backing guarantees, and **Critical** impact: irreversible fund lock / permanently unclaimable user value. [5](#0-4) 

### Likelihood Explanation
- `initTransfer` is a fully public, permissionless function — no role or whitelist check guards the standard ERC-20 path.
- Fee-on-transfer tokens are a well-known ERC-20 pattern (deflationary tokens, tokens with protocol fees, rebasing tokens with transfer taxes).
- The deficit accumulates silently across all users of the affected token; no single transaction is obviously malicious.
- An attacker can deliberately deploy and bridge a fee-on-transfer token to create unbacked supply on NEAR, or a legitimate token that later enables fees (via an upgradeable contract) can trigger the same condition retroactively. [6](#0-5) 

### Recommendation
Replace the static `amount` parameter with an actual balance-delta measurement:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived == amount, "Fee-on-transfer token not supported");
```

Alternatively, explicitly document and enforce that fee-on-transfer tokens are not supported by adding a token registry/whitelist that gates `initTransfer` for the standard ERC-20 path.

### Proof of Concept
1. Deploy a fee-on-transfer ERC-20 token `T` with a 10% transfer fee.
2. Call `OmniBridge.initTransfer(T, 1000, 0, 0, "near:alice.near", "")`.
   - `safeTransferFrom` moves 1000 T from the caller; the bridge receives 900 T (10% fee taken).
   - `InitTransfer` event is emitted with `amount = 1000`.
3. The NEAR relayer picks up the event and calls `fin_transfer` on NEAR, crediting Alice with 1000 T.
4. Alice bridges back 1000 T from NEAR to EVM.
   - NEAR burns/locks 1000 T and emits a cross-chain message for 1000 T.
   - `finTransfer` on EVM calls `safeTransfer(alice, 1000)` but the bridge only holds 900 T → **revert**.
5. Alice's 1000 T on NEAR is consumed but she receives nothing on EVM — funds are permanently locked.

Repeated by multiple users, the deficit compounds until the bridge is fully insolvent for token `T`. [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-436)
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
```

**File:** near/omni-types/src/prover_result.rs (L9-18)
```rust
pub struct InitTransferMessage {
    pub origin_nonce: Nonce,
    pub token: OmniAddress,
    pub amount: U128,
    pub recipient: OmniAddress,
    pub fee: Fee,
    pub sender: OmniAddress,
    pub msg: String,
    pub emitter_address: OmniAddress,
}
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

**File:** near/omni-bridge/src/lib.rs (L726-749)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
```
