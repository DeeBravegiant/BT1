### Title
Uncapped Gas Forwarding in `transferAndCall` Enables Block Stuffing — (`contracts/ccip/ERC677.sol`)

---

### Summary

`ERC677.transferAndCall` forwards all remaining gas to an arbitrary receiver's `onTokenTransfer` callback with no gas stipend or cap. Any unprivileged `WrappedRSETH` holder can point this call at a self-deployed gas-burning contract and consume nearly the entire block gas limit in a single transaction.

---

### Finding Description

`WrappedRSETH` inherits `ERC677` and exposes `transferAndCall` with no access control override. The function:

1. Transfers tokens via `super.transfer` (standard ERC20, no guard against arbitrary `to`)
2. Checks `to.isContract()` — if true, calls `onTokenTransfer` with **all remaining gas forwarded** and no cap [1](#0-0) 

The `validAddress` modifier in `WrappedRSETH` only blocks `address(this)` as recipient — it does **not** restrict `transferAndCall` at all, since `transferAndCall` is inherited from `ERC677` and not overridden. [2](#0-1) 

The `IERC677Receiver(to).onTokenTransfer(...)` call at line 32 of `ERC677.sol` uses a plain high-level call, which forwards all remaining gas by default — there is no `{gas: N}` stipend. [3](#0-2) 

---

### Impact Explanation

An attacker holding even 1 wei of `WrappedRSETH` can:

1. Deploy a `GasBurner` contract whose `onTokenTransfer` spins in an infinite loop.
2. Call `transferAndCall(gasEater, 1, "")` with `gas = block.gaslimit - overhead`.
3. The callback receives all remaining gas and exhausts it.

This fills the entire block with a single transaction, temporarily preventing all other protocol transactions (deposits, withdrawals, CCIP messages) from being included in that block — matching the **"Low. Block stuffing"** impact scope.

---

### Likelihood Explanation

- **Precondition:** Any nonzero `WrappedRSETH` balance — achievable by anyone who has bridged via CCIP.
- **Cost:** The attacker must pay for all gas consumed. On Ethereum mainnet this is expensive; on cheaper L2s (where CCIP-bridged `WrappedRSETH` is more likely deployed) the cost is significantly lower.
- **No privileged access required:** `transferAndCall` is a public, permissionless function with no role check.

Likelihood is **low** due to economic cost, but the path is fully concrete and requires no admin compromise.

---

### Recommendation

Apply a gas cap when invoking the `onTokenTransfer` callback, e.g.:

```solidity
// Forward at most a bounded amount of gas to the receiver
uint256 GAS_LIMIT = 100_000;
IERC677Receiver(to).onTokenTransfer{gas: GAS_LIMIT}(msg.sender, amount, data);
```

Alternatively, follow Chainlink's own `BurnMintERC677` reference implementation, which uses a fixed gas stipend for the callback, or add a whitelist of approved receiver contracts.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import { IERC677Receiver } from "contracts/ccip/ERC677.sol";

contract GasBurner is IERC677Receiver {
    function onTokenTransfer(address, uint256, bytes calldata) external override {
        // Spin until all forwarded gas is exhausted
        uint256 i;
        while (true) { unchecked { i++; } }
    }
}

// Test (Foundry):
// 1. Deploy WrappedRSETH, mint 1 wei to attacker
// 2. Deploy GasBurner
// 3. vm.prank(attacker); wrappedRSETH.transferAndCall(address(gasBurner), 1, "");
// 4. Assert gasleft() at return is near 0 and tx consumed >= block.gaslimit - 21000
``` [1](#0-0) [4](#0-3)

### Citations

**File:** contracts/ccip/ERC677.sol (L28-35)
```text
    function transferAndCall(address to, uint256 amount, bytes memory data) external returns (bool success) {
        super.transfer(to, amount);
        emit Transfer(msg.sender, to, amount, data);
        if (to.isContract()) {
            IERC677Receiver(to).onTokenTransfer(msg.sender, amount, data);
        }
        return true;
    }
```

**File:** contracts/ccip/WrappedRSETH.sol (L20-20)
```text
contract WrappedRSETH is IBurnMintERC20, ERC677, IERC165, ERC20Burnable, ConfirmedOwnerWithProposal {
```

**File:** contracts/ccip/WrappedRSETH.sol (L102-106)
```text
    modifier validAddress(address recipient) virtual {
        // solhint-disable-next-line reason-string, custom-errors
        if (recipient == address(this)) revert();
        _;
    }
```
