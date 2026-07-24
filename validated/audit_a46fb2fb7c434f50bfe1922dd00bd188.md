I was unable to read the full implementation of the critical functions in `near/omni-bridge/src/lib.rs` due to tool iteration limits. The grep searches confirmed that `utxo_fin_transfer`, `utxo_fin_transfer_to_near_callback`, `resolve_utxo_fin_transfer`, `is_refund_required`, `remove_fin_utxo_transfer`, and `finalised_utxo_transfers` all exist in that file, but I could not retrieve their actual source code to trace the exact control flow.

Without reading the concrete implementation of these functions, I cannot:

1. Confirm whether `utxo_fin_transfer` has a connector/privilege guard or is callable by any account.
2. Verify whether `remove_fin_utxo_transfer` is invoked on the partial-rejection branch (i.e., when `is_refund_required` returns true), which is the crux of the double-finalization claim.
3. Confirm whether the `finalised_utxo_transfers` set is populated *before* the async `ft_transfer_call` is dispatched (which would prevent re-entry) or only after the callback resolves.
4. Determine whether the `ft_transfer_call` path is actually reachable by an unprivileged caller via a non-empty `msg` field.

The only production file I could fully read was `near/omni-bridge/src/btc.rs`, which covers the outbound BTC withdrawal path and does not contain the inbound UTXO finalization logic in question. [1](#0-0) 

**Due to index size limits, the full contents of `near/omni-bridge/src/lib.rs` were not retrievable**, which is the sole file containing all functions under audit. I recommend starting a Devin session to read the complete implementation and perform a definitive trace of the `utxo_fin_transfer` → `resolve_utxo_fin_transfer` → `remove_fin_utxo_transfer` path before rendering a verdict on this question.

### Citations

**File:** near/omni-bridge/src/btc.rs (L1-30)
```rust
use crate::storage::NEP141_DEPOSIT;
use crate::{
    ext_token, ext_utxo_connector, Contract, ContractExt, Role, FT_TRANSFER_CALL_GAS, ONE_YOCTO,
    STORAGE_DEPOSIT_GAS,
};
use near_plugins::{access_control_any, pause, AccessControllable, Pausable};
use near_sdk::json_types::U128;
use near_sdk::{
    env, near, require, serde_json, AccountId, Gas, NearToken, Promise, PromiseError,
    PromiseOrValue,
};
use omni_types::btc::{TokenReceiverMessage, TxOut, UTXOChainConfig};
use omni_types::errors::BridgeError;
use omni_types::{
    get_native_token_address, ChainKind, DestinationChainMsg, Fee, TransferId, TransferMessage,
};
use omni_utils::macros::trusted_relayer;
use omni_utils::near_expect::NearExpect;

const SUBMIT_TRANSFER_TO_BTC_CONNECTOR_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const WITHDRAW_RBF_GAS: Gas = Gas::from_tgas(100);

#[trusted_relayer]
#[near]
impl Contract {
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn submit_transfer_to_utxo_chain_connector(
        &mut self,
```
