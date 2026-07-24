[1](#0-0) [2](#0-1)

### Citations

**File:** near/omni-bridge/src/lib.rs (L224-247)
```rust
pub struct Contract {
    pub factories: LookupMap<ChainKind, OmniAddress>,
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
    pub finalised_utxo_transfers: LookupSet<UnifiedTransferId>,
    pub fast_transfers: LookupMap<FastTransferId, FastTransferStatusStorage>,
    pub token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>,
    pub token_address_to_id: LookupMap<OmniAddress, AccountId>,
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
    pub deployed_tokens: LookupSet<AccountId>,
    pub deployed_tokens_v2: LookupMap<AccountId, ChainKind>,
    pub token_deployer_accounts: LookupMap<ChainKind, AccountId>,
    pub mpc_signer: AccountId,
    pub current_origin_nonce: Nonce,
    // We maintain a separate nonce for each chain to optimize the storage usage on Solana by reducing the gaps.
    pub destination_nonces: LookupMap<ChainKind, Nonce>,
    pub accounts_balances: LookupMap<AccountId, StorageBalance>,
    pub wnear_account_id: AccountId,
    pub provers: UnorderedMap<ChainKind, AccountId>,
    pub init_transfer_promises: LookupMap<AccountId, CryptoHash>,
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
}
```
