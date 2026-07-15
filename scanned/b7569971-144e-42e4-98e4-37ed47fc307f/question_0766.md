# Q0766: hex to nibbles state root consistency

## Question

What can an unprivileged user do by writing contract storage, creating/deleting accounts, and generating state/proof boundary cases through valid transactions so that `hex_to_nibbles` in `core/store/src/trie/mem/nibbles_utils.rs` processes contract storage keys, account creation/deletion actions, receipts, trie proofs, and state-part boundaries along the trie, flat storage, state sync, and proofs path? User controls contract storage keys, account creation/deletion actions, receipts, trie proofs, and state-part boundaries -> `hex_to_nibbles` processes that value during TrieUpdate writes, flat-state reads, state sync proof assembly, and chunk extra/state-root computation -> the trie state, flat state, storage usage, and committed state roots represent the same deterministic post-state invariant might break -> potential in-scope impact is state desynchronization, storage corruption, balance manipulation, or consensus flaw under the NEAR HackenProof scope. Exploit hypothesis: a user-controlled storage mutation can make this code commit a state root that disagrees with account storage accounting or flat-state contents, violating the actual protocol invariant that trie state, flat state, storage usage, and committed state roots represent the same deterministic post-state.

## Target

- File/function: core/store/src/trie/mem/nibbles_utils.rs:9::hex_to_nibbles
- Entrypoint: contract storage and account actions committed through Runtime::apply into core/store trie and flat-state paths
- User-controlled input: contract storage keys, account creation/deletion actions, receipts, trie proofs, and state-part boundaries
- Attack path: User controls contract storage keys, account creation/deletion actions, receipts, trie proofs, and state-part boundaries -> public entrypoint reaches `hex_to_nibbles` -> TrieUpdate writes, flat-state reads, state sync proof assembly, and chunk extra/state-root computation handles the value -> invariant failure could produce state desynchronization, storage corruption, balance manipulation, or consensus flaw
- Security invariant: trie state, flat state, storage usage, and committed state roots represent the same deterministic post-state
- Expected bounty impact: state desynchronization, storage corruption, balance manipulation, or consensus flaw
- Fast validation approach: drive account/storage mutations through blocks, state sync, and restart paths while comparing trie root, flat state, storage usage, and execution outcomes
