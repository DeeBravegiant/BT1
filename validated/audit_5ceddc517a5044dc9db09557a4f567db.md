Audit Report

## Title
Hardcoded Proxy Nonce `0x01` in `getDeployed()` Causes `CREATE3Factory.deploy()` to Always Revert on zkSync Era - (File: contracts/utils/CREATE3.sol)

## Summary
`CREATE3.getDeployed()` hardcodes the proxy nonce as `hex"01"` when computing the child contract's deterministic address, relying on EIP-161's rule that a contract's deployment nonce is incremented to `1` before its constructor runs. zkSync Era does not implement EIP-161: the proxy's deployment nonce remains `0` when it executes `CREATE`, placing the child at a different address than predicted. Every call to `CREATE3Factory.deploy()` on zkSync Era reverts with `"INITIALIZATION_FAILED"`, making the factory entirely non-functional on that chain.

## Finding Description
In `contracts/utils/CREATE3.sol`, `getDeployed()` computes the child address by RLP-encoding the proxy address with a hardcoded nonce of `1`:

```solidity
// L61-64
return keccak256(abi.encodePacked(hex"d694", proxy, hex"01")) // Nonce of the proxy contract (1)
    .fromLast20Bytes();
```

This is correct on standard EVM chains where EIP-161 mandates the nonce is set to `1` before the constructor executes. On zkSync Era, this increment does not occur: the proxy's deployment nonce is `0` when it issues `CREATE`, so the child lands at the address derived from nonce `0`.

In `deploy()`, the predicted address is captured before the proxy call:

```solidity
// L48-50
deployed = getDeployed(salt);
(bool success,) = proxy.call{ value: value }(creationCode);
require(success && deployed.code.length != 0, "INITIALIZATION_FAILED");
```

`deployed` points to the nonce-1 address; the actual child was placed at the nonce-0 address. `deployed.code.length` is always `0`, so the `require` always reverts. The proxy `CREATE2` deployment at L44 succeeds, but the child deployment result is discarded. No funds are locked because the revert returns `msg.value`, but the factory's core function is broken.

zkSync Era is an explicitly supported chain in this project: `foundry.toml` includes a `zksync` RPC endpoint, and the README references zkSync Era 16 times. `CREATE3Factory` is documented as designed for cross-chain deterministic deployment (`"This factory can be deployed at the same address on multiple chains"`).

## Impact Explanation
**Low — Contract fails to deliver promised returns, but does not lose value.**

The factory's sole purpose is deterministic cross-chain deployment. On zkSync Era, every invocation of `deploy()` reverts unconditionally. No user funds are permanently lost (reverts return `msg.value`), but the contract cannot fulfill its stated cross-chain deployment guarantee on a chain the protocol explicitly supports.

## Likelihood Explanation
`CREATE3Factory.deploy()` is `external payable` with no access control — any user can call it. The factory's stated purpose invites use on all supported chains including zkSync Era. The failure is deterministic and triggered on the very first call; no special conditions, attacker capability, or repeated attempts are needed. Any developer or protocol attempting to use the factory on zkSync Era will hit this immediately.

## Recommendation
Replace the hardcoded `hex"01"` with a chain-aware nonce encoding. On zkSync Era, nonce `0` RLP-encodes as `hex"80"` (RLP encoding of integer 0); on standard EVM chains it remains `hex"01"`:

```solidity
// Detect zkSync Era by chain ID (280 = testnet, 324 = mainnet)
bool isZkSync = block.chainid == 324 || block.chainid == 280;
bytes memory encoded = isZkSync
    ? abi.encodePacked(hex"d694", proxy, hex"80") // nonce=0
    : abi.encodePacked(hex"d694", proxy, hex"01"); // nonce=1
return keccak256(encoded).fromLast20Bytes();
```

Alternatively, adopt a zkSync-aware CREATE3 library (e.g., `matter-labs/era-contracts` patterns) or explicitly document and enforce that `CREATE3Factory` must not be deployed on zkSync Era.

## Proof of Concept
1. Deploy `CREATE3Factory` on zkSync Era (chain ID 324).
2. Call `deploy(salt, creationCode)` with any valid `creationCode`.
3. `CREATE2` at L44 deploys the proxy successfully; `proxy != address(0)`.
4. `getDeployed(salt)` at L48 returns address `B` (derived from nonce `1`).
5. `proxy.call{value: value}(creationCode)` at L49 succeeds; the child is deployed at address `A` (derived from nonce `0`, `A ≠ B`).
6. `deployed.code.length` checks `B.code.length == 0` → `require` fails.
7. Transaction reverts with `"INITIALIZATION_FAILED"`.

Foundry fork test plan:
```solidity
function testZkSyncCreate3Fails() public {
    // Fork zkSync Era mainnet (chain ID 324)
    vm.createSelectFork(vm.rpcUrl("zksync"));
    CREATE3Factory factory = new CREATE3Factory();
    bytes32 salt = keccak256("test");
    // Deploy any trivial contract
    bytes memory code = type(SimpleContract).creationCode;
    vm.expectRevert("INITIALIZATION_FAILED");
    factory.deploy(salt, code);
}
```