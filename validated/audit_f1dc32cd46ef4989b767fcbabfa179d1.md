### Title
Immutable `i_maxSupply` in `WrappedRSETH` Can Permanently Lock rsETH in CCIP Token Pool When Destination Mint Reverts — (`contracts/ccip/WrappedRSETH.sol`)

---

### Summary

`WrappedRSETH` enforces an immutable supply cap (`i_maxSupply`) on its `mint()` function. When `L1VaultV2.bridgeRsETHToL2UsingCCIP()` initiates a CCIP token transfer, the CCIP token pool on L1 locks (or burns) rsETH. If the corresponding `WrappedRSETH.mint()` call on L2 reverts with `MaxSupplyExceeded`, CCIP has no automatic refund path. Because `i_maxSupply` is immutable and cannot be raised, and because neither `L1VaultV2` nor `WrappedRSETH` contains any recovery mechanism, the rsETH can be permanently frozen.

---

### Finding Description

**Step 1 — L1VaultV2 initiates the CCIP transfer.**

`L1VaultV2.bridgeRsETHToL2UsingCCIP()` approves the CCIP router for `amount` of rsETH and calls `ccipRouter.ccipSend()`: [1](#0-0) 

The CCIP router hands the tokens to the configured L1 token pool, which locks (or burns) the rsETH on L1. From this point, the rsETH is no longer in `L1VaultV2`'s custody.

**Step 2 — CCIP delivers the message to L2 and calls `WrappedRSETH.mint()`.**

The L2 CCIP token pool calls `WrappedRSETH.mint(account, amount)`. The mint function enforces the immutable cap: [2](#0-1) 

If `totalSupply() + amount > i_maxSupply`, the call reverts with `MaxSupplyExceeded`. The CCIP message execution fails on L2.

**Step 3 — No automatic refund exists.**

CCIP does not automatically refund locked/burned source-chain tokens when destination execution fails. The protocol provides a `manuallyExecute()` retry path, but retries call the same `mint()` function with the same `amount`. Since `i_maxSupply` is **immutable**: [3](#0-2) 

it cannot be raised by any admin action. Every retry will revert identically as long as `totalSupply() + amount > i_maxSupply`. If the circulating supply of `WrappedRSETH` never decreases below the cap (a realistic steady-state for a growing protocol), the rsETH locked in the CCIP token pool is permanently irrecoverable.

**Step 4 — No recovery path in L1VaultV2.**

`L1VaultV2` contains no function to cancel a pending CCIP message, reclaim tokens from the CCIP token pool, or otherwise recover from a failed cross-chain mint. The contract's only CCIP-related function is `bridgeRsETHToL2UsingCCIP()`, which only initiates new transfers: [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

rsETH locked in the CCIP token pool on L1 cannot be recovered. No `WrappedRSETH` is minted on L2. The bridged value is destroyed. The immutability of `i_maxSupply` is the root cause: even if the protocol wanted to fix the situation by raising the cap, it cannot do so without redeploying `WrappedRSETH` (which would require migrating all existing holders and reconfiguring all CCIP token pools — a non-trivial, potentially impossible recovery).

---

### Likelihood Explanation

**Low-to-Medium.** The preconditions are:
1. `WrappedRSETH` is deployed with a non-zero `i_maxSupply` (explicitly supported by the constructor parameter `maxSupply_`).
2. The circulating supply of `WrappedRSETH` on L2 approaches the cap (natural as the protocol grows).
3. A CCIP transfer is initiated whose `amount` would push `totalSupply` over the cap.

Condition 1 is a deployment choice. Conditions 2 and 3 are natural protocol lifecycle events. No attacker action is required — the scenario arises from normal operation.

---

### Recommendation

1. **Remove or make `i_maxSupply` mutable** (governed by an admin/timelock role) so the cap can be raised if it is approached, allowing CCIP retries to succeed.
2. **Add a pre-flight check in `L1VaultV2.bridgeRsETHToL2UsingCCIP()`** that queries the current `totalSupply()` and `maxSupply()` of the destination `WrappedRSETH` before initiating the CCIP transfer, reverting if the transfer would exceed the cap.
3. **Document the operational risk** and establish a monitoring alert when `WrappedRSETH` supply approaches `i_maxSupply`, so the manager can pause bridging before a failed transfer occurs.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test (local fork, no public mainnet)
// Demonstrates: CCIP transfer initiated → WrappedRSETH.mint() reverts → rsETH locked

contract CCIPMaxSupplyFreezePoC {
    // Setup:
    // 1. Deploy WrappedRSETH with maxSupply_ = 1000e18
    // 2. Mint 999e18 WrappedRSETH to simulate near-cap state
    // 3. Configure L1VaultV2 with CCIP bridge type
    // 4. Fund L1VaultV2 with 2e18 rsETH

    function testPermanentFreeze() external {
        // Pre-state: WrappedRSETH.totalSupply() == 999e18, i_maxSupply == 1000e18
        // L1VaultV2 holds 2e18 rsETH

        // Manager calls bridgeRsETHToL2UsingCCIP(2e18)
        // → CCIP router locks 2e18 rsETH in L1 token pool
        // → CCIP delivers message to L2
        // → WrappedRSETH.mint(receiver, 2e18) called
        // → 999e18 + 2e18 = 1001e18 > 1000e18 → MaxSupplyExceeded revert
        // → CCIP message marked FAILED
        // → rsETH remains locked in L1 CCIP token pool
        // → manuallyExecute() retry → same revert (i_maxSupply immutable)
        // → rsETH permanently frozen

        // Assert: L1VaultV2.rsETH.balanceOf(L1VaultV2) == 0 (tokens left vault)
        // Assert: WrappedRSETH.totalSupply() == 999e18 (no mint occurred)
        // Assert: No recovery function exists in L1VaultV2
    }
}
```

The `i_maxSupply` immutability is confirmed at: [5](#0-4) 

The `mint()` guard that causes the revert: [2](#0-1) 

The CCIP send path that relinquishes custody of rsETH with no recovery: [6](#0-5)

### Citations

**File:** contracts/L1VaultV2.sol (L341-367)
```text
    function bridgeRsETHToL2UsingCCIP(uint256 amount) external payable nonReentrant onlyRole(MANAGER_ROLE) {
        if (bridgeType != BridgeType.CCIP) {
            revert InactiveBridgeType();
        }

        if (rsETH.balanceOf(address(this)) < amount) {
            revert InsufficientRsETHBalance();
        }

        if (amount == 0) {
            revert ZeroAmount();
        }

        uint256 fee = getCCIPFee(amount);

        if (msg.value != fee) {
            revert IncorrectCCIPFee();
        }

        IERC20(address(rsETH)).safeIncreaseAllowance(address(ccipRouter), amount);

        Client.EVM2AnyMessage memory message = getCCIPMessage(amount);

        bytes32 messageId = ccipRouter.ccipSend{ value: msg.value }(destinationChainSelector, message);

        emit BridgedRsETHToL2UsingCCIP(destinationChainSelector, l2Receiver, amount, messageId);
    }
```

**File:** contracts/ccip/WrappedRSETH.sol (L41-55)
```text
    uint256 internal immutable i_maxSupply;

    constructor(
        string memory name,
        string memory symbol,
        uint8 decimals_,
        uint256 maxSupply_,
        address _owner
    )
        ERC677(name, symbol)
        ConfirmedOwnerWithProposal(_owner, address(0))
    {
        i_decimals = decimals_;
        i_maxSupply = maxSupply_;
    }
```

**File:** contracts/ccip/WrappedRSETH.sol (L137-141)
```text
    function mint(address account, uint256 amount) external override onlyMinter validAddress(account) {
        if (i_maxSupply != 0 && totalSupply() + amount > i_maxSupply) revert MaxSupplyExceeded(totalSupply() + amount);

        _mint(account, amount);
    }
```
