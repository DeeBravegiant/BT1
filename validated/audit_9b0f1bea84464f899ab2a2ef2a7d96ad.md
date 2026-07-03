Audit Report

## Title
`account != msg.sender` guard in `_processClaim` permanently freezes unclaimed KERNEL yield for contract recipients unable to self-call — (`contracts/KERNEL/KernelMerkleDistributor.sol`)

## Summary
`_processClaim` unconditionally reverts with `Unauthorized` whenever `account != msg.sender`, meaning only the account itself can initiate a claim. Any smart contract address included in the merkle tree that lacks explicit logic to call `claim` or `claimAndStake` on this distributor can never collect its allocated KERNEL yield. No admin sweep, expiry mechanism, or alternative claim path exists in the contract, making the freeze permanent.

## Finding Description
In `_processClaim` at lines 311–313:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Both public entry-points delegate to this internal function: `claim` at line 261 and `claimAndStake` at line 280. The guard unconditionally requires the caller to be the account itself. For EOAs this is always satisfiable. For a smart contract, it is satisfiable only if that contract contains code that explicitly calls `claim`/`claimAndStake` on this distributor.

Contracts that lack such logic — simple vaults, DAO treasuries, pure-receive contracts, proxies without a generic `execute` function, or any contract whose upgrade path removed the relevant function — can never satisfy `account == msg.sender`. No third-party relayer, keeper, or EOA can substitute, because every such attempt reverts with `Unauthorized`.

The admin functions (lines 348–424) provide no rescue path: there is no sweep function, no expiry, and no fallback claim path. Tokens allocated to such an account remain in the distributor's balance indefinitely, unreachable by the intended recipient and by anyone else.

Crucially, the merkle proof already cryptographically binds `(index, account, cumulativeAmount)` at line 320–322, and `kernel.safeTransfer(account, amountToSend)` at line 263 always sends funds to `account`, not to `msg.sender`. The identity check therefore provides no additional security — it only restricts reachability.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

KERNEL tokens allocated to a contract-address recipient that cannot self-call `claim` remain locked in the distributor indefinitely. The tokens are not lost from the contract's balance, but they are unreachable by the intended recipient and by anyone else. This exactly matches the allowed impact: *Medium. Permanent freezing of unclaimed yield.*

## Likelihood Explanation
The merkle tree is built off-chain from on-chain activity (deposits, staking, etc.). Any contract that interacted with the protocol — a vault, a DAO treasury, a simple proxy — may legitimately appear as a leaf. The `IMerkleDistributor` interface imposes no restriction against contract addresses as recipients. The likelihood that at least one such address appears in a live distribution is realistic, not theoretical, and the freeze is triggered simply by the contract being included in the tree and being unable to self-call.

## Recommendation
Replace the blanket identity check with an explicit authorization model. Two standard approaches:

1. **Remove the check entirely** — the merkle proof already cryptographically binds `(index, account, cumulativeAmount)`, and `safeTransfer` always sends to `account`, so there is no security regression.

2. **Allow approved operators per account** — let each account pre-register addresses that may claim on its behalf:
   ```solidity
   mapping(address account => mapping(address operator => bool)) public approvedOperators;

   if (account != msg.sender && !approvedOperators[account][msg.sender]) {
       revert Unauthorized();
   }
   ```

Option 1 is simpler and has no security regression.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Minimal contract with NO external-call capability
contract NoCallVault {
    receive() external payable {}
}

contract PoC {
    KernelMerkleDistributor distributor;
    NoCallVault vault; // address(vault) is a leaf in the merkle tree

    function test() external {
        uint256 index = 1;
        uint256 amount = 1e18;
        bytes32[] memory proof = /* valid proof for (index, address(vault), amount) */ new bytes32[](0);

        // Any caller other than address(vault) → Unauthorized
        try distributor.claim(index, address(vault), amount, proof) {
            revert("should have reverted");
        } catch {
            // reverts with Unauthorized — yield is permanently frozen
        }

        // address(vault) itself cannot produce an outbound call to claim()
        // → no reachable path exists; allocation is permanently frozen
    }
}
```

The vault's allocation is provably unreachable: the only caller satisfying `account == msg.sender` is `address(vault)` itself, but `NoCallVault` contains no function that can produce an outbound call to `claim`.