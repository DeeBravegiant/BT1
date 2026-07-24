### Title
User-Provided `max_gas_fee` in UTXO Withdrawal Permanently Locks Funds When Below Connector Minimum — (File: `near/omni-bridge/src/btc.rs`)

### Summary
A user initiating a BTC/Zcash withdrawal can embed a `MaxGasFee` value in the transfer `msg` field at `initTransfer` time. If that value is below the BTC connector's `min_btc_gas_fee`, the connector rejects the withdrawal, the transfer is re-inserted into `pending_transfers` with the same immutable `msg`, and there is no mechanism to update `max_gas_fee` or cancel the transfer. The user's funds are permanently locked.

### Finding Description

**Step 1 — User sets `max_gas_fee` at initiation.**

`InitTransferMsg.msg` accepts a JSON-encoded `DestinationChainMsg::MaxGasFee(U64)` value, which is stored verbatim in `TransferMessage.msg`. [1](#0-0) 

**Step 2 — Relayer is forced to use the exact stored value.**

In `submit_transfer_to_utxo_chain_connector`, the bridge reads the stored `max_gas_fee` from `transfer.message.msg` and requires the relayer's `max_gas_fee` to match it exactly. There is no floor check against the connector's `min_btc_gas_fee`. [2](#0-1) 

**Step 3 — BTC connector rejects the withdrawal.**

The bridge calls `ft_transfer_call` to the BTC connector, forwarding the original `msg` (including the undersized `max_gas_fee`). The BTC connector enforces its own `min_btc_gas_fee` (e.g., `100` sat in the deployed config) and rejects the call by returning the full token amount as a refund. [3](#0-2) 

**Step 4 — Transfer is re-inserted with the same immutable `msg`.**

The callback detects the rejection (`result.0 == 0`) and re-inserts the transfer into `pending_transfers` with the original `TransferMessage`, including the unchanged `max_gas_fee`. [4](#0-3) 

**Step 5 — No recovery path exists.**

- `update_fee` only updates `fee` and `native_fee`; the `msg` field (containing `MaxGasFee`) is immutable after `initTransfer`.
- There is no cancel or refund function for UTXO-bound transfers.
- The relayer cannot override `max_gas_fee` because the exact-match check at line 59 will always reject a corrected value. [5](#0-4) 

### Impact Explanation

Funds are permanently locked in `pending_transfers` with no protocol-level path to recover them. The only escape would be an admin lowering the BTC connector's `min_btc_gas_fee` below the user-specified value — an out-of-band privileged action that is not guaranteed. This matches the allowed impact: **Critical — Irreversible fund lock / frozen redemption path in UTXO flows.**

### Likelihood Explanation

Medium. Any user who specifies `{"MaxGasFee": N}` with `N` below the connector's current `min_btc_gas_fee` triggers this path. The BTC fee market is volatile; a value that was acceptable at initiation time may fall below the connector's minimum by the time the relayer submits. Users have no on-chain way to discover the connector's minimum before initiating.

### Recommendation

1. **Validate `max_gas_fee` at initiation time** against the connector's `min_btc_gas_fee` (requires a cross-contract view or a cached minimum stored in the bridge).
2. **Allow updating the `msg` field** (or at least the `max_gas_fee` sub-field) via an `update_fee`-style function, restricted to the original sender.
3. **Add a cancel/refund function** for UTXO-bound transfers that have been re-inserted after connector rejection, so users can recover locked funds.

### Proof of Concept

1. User calls `ft_transfer_call` on the NEAR bridge with `msg: InitTransfer { recipient: "btc:...", msg: Some("{\"MaxGasFee\":1}"), fee: ..., native_fee: ... }`.
2. Bridge stores `TransferMessage { msg: "{\"MaxGasFee\":1}", ... }` in `pending_transfers`.
3. Trusted relayer calls `submit_transfer_to_utxo_chain_connector` with `msg: Withdraw { max_gas_fee: Some(1), ... }` — the only value that passes the exact-match check.
4. BTC connector's `ft_on_transfer` rejects because `1 < min_btc_gas_fee (100)`, returning the full token amount.
5. `submit_transfer_to_btc_connector_callback` receives `Ok(0)` (or `Ok(full_amount)` refunded), re-inserts the transfer unchanged.
6. All subsequent relayer attempts with `max_gas_fee = 1` are rejected by the connector; any attempt with a higher value is rejected by the bridge's exact-match check at `btc.rs:59`.
7. User's BTC tokens are permanently locked in `pending_transfers`. [2](#0-1) [6](#0-5)

### Citations

**File:** near/omni-types/src/lib.rs (L504-516)
```rust
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct InitTransferMsg {
    pub recipient: OmniAddress,
    pub fee: U128,
    pub native_token_fee: U128,
    /// Optional caller-supplied destination-chain hook payload. Length-capped to
    /// [`MAX_INIT_TRANSFER_MSG_LEN`] bytes to prevent unbounded storage/gas inflation.
    pub msg: Option<BoundedString<MAX_INIT_TRANSFER_MSG_LEN>>,
    /// Optional caller-provided identifier mixed into the virtual storage account ID hash.
    /// Lets otherwise-identical transfers derive distinct storage accounts so their
    /// storage deposits do not collide. Length-capped to [`MAX_EXTERNAL_ID_LEN`] bytes.
    pub external_id: Option<BoundedString<MAX_EXTERNAL_ID_LEN>>,
}
```

**File:** near/omni-types/src/lib.rs (L935-962)
```rust
#[near(serializers=[json])]
#[derive(Debug, PartialEq)]
pub enum DestinationChainMsg {
    MaxGasFee(U64),
    DestHexMsg(#[serde_as(as = "Hex")] Vec<u8>),
}

impl DestinationChainMsg {
    pub fn max_gas_fee(&self) -> Option<U128> {
        if let Self::MaxGasFee(fee) = self {
            Some(U128(fee.0.into()))
        } else {
            None
        }
    }

    pub fn destination_msg(&self) -> Option<Vec<u8>> {
        if let Self::DestHexMsg(msg) = self {
            Some(msg.clone())
        } else {
            None
        }
    }

    pub fn from_json(s: &str) -> Option<Self> {
        serde_json::from_str(s).ok()
    }
}
```

**File:** near/omni-bridge/src/btc.rs (L54-62)
```rust
                let max_gas_fee_msg = DestinationChainMsg::from_json(&transfer.message.msg)
                    .and_then(|s| s.max_gas_fee());

                if let Some(max_gas_fee_msg) = max_gas_fee_msg {
                    require!(
                        max_gas_fee.expect("max_gas_fee is missing") == max_gas_fee_msg,
                        "Invalid max gas fee"
                    );
                }
```

**File:** near/omni-bridge/src/btc.rs (L88-101)
```rust
        ext_token::ext(btc_account_id)
            .with_attached_deposit(ONE_YOCTO)
            .with_static_gas(FT_TRANSFER_CALL_GAS)
            .ft_transfer_call(self.get_utxo_chain_connector(chain_kind), amount, None, msg)
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SUBMIT_TRANSFER_TO_BTC_CONNECTOR_CALLBACK_GAS)
                    .submit_transfer_to_btc_connector_callback(
                        transfer.message,
                        transfer.owner,
                        fee_recipient,
                    ),
            )
    }
```

**File:** near/omni-bridge/src/btc.rs (L104-126)
```rust
    pub fn submit_transfer_to_btc_connector_callback(
        &mut self,
        transfer_msg: TransferMessage,
        transfer_owner: AccountId,
        fee_recipient: AccountId,
        #[callback_result] call_result: &Result<U128, PromiseError>,
    ) -> PromiseOrValue<()> {
        if matches!(call_result, Ok(result) if result.0 > 0) {
            let token_fee = transfer_msg.fee.fee.0;
            self.send_fee_internal(&transfer_msg, fee_recipient, token_fee)
        } else {
            let required_storage_balance =
                self.add_transfer_message(transfer_msg, transfer_owner.clone());

            self.update_storage_balance(
                transfer_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            );

            PromiseOrValue::Value(())
        }
    }
```
