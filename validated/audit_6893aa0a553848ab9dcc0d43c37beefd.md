### Title
Block Stuffing Causes `stake32EthValidated()` to Revert via Stale `expectedDepositRoot`, Temporarily Preventing ETH Staking — (`contracts/NodeDelegator.sol`)

---

### Summary

`NodeDelegator.stake32EthValidated()` compares an operator-supplied `expectedDepositRoot` against the live `IETHPOSDeposit.get_deposit_root()` at execution time. Because the ETH2 deposit contract's root advances with every new validator deposit, an attacker can perform block stuffing — filling blocks with transactions — to delay the operator's transaction long enough for the root to change, causing a guaranteed revert. The 32 ETH remains idle in the NDC, `stakedButUnverifiedNativeETH` is never incremented, and restaking yield is not earned for the duration of the attack.

---

### Finding Description

`stake32EthValidated` is defined as:

```solidity
function stake32EthValidated(
    bytes calldata pubkey,
    bytes calldata signature,
    bytes32 depositDataRoot,
    bytes32 expectedDepositRoot
)
    external          // ← no onlyLRTOperator here
{
    IETHPOSDeposit depositContract = _getEigenPodManager().ethPOS();
    bytes32 actualDepositRoot = depositContract.get_deposit_root();
    if (expectedDepositRoot != actualDepositRoot) {
        revert InvalidDepositRoot(expectedDepositRoot, actualDepositRoot);
    }
    stake32Eth(pubkey, signature, depositDataRoot);
}
``` [1](#0-0) 

The deposit root check is a snapshot-at-submission guard: the operator reads `get_deposit_root()` offchain, encodes it as `expectedDepositRoot`, and submits the transaction. If any deposit to the ETH2 deposit contract lands between that read and the transaction's inclusion, the root changes and the call reverts with `InvalidDepositRoot`. [2](#0-1) 

The ETH2 deposit contract is a permissionless, globally shared contract — any party can submit a deposit at any time, advancing the root. Block stuffing amplifies this: the attacker fills consecutive blocks with high-gas-price transactions, preventing the operator's transaction from being included while the root continues to advance. The attack does not require the attacker to make deposits themselves; organic validator onboarding activity is sufficient to change the root during the stuffed window.

When the revert occurs:

- `stakedButUnverifiedNativeETH` is **not** incremented (that increment is inside `stake32Eth`, which is never reached).
- The 32 ETH remains in the NDC balance, idle and not earning restaking yield.
- The operator must recompute `expectedDepositRoot` and resubmit, but the attacker can repeat the stuffing cycle. [3](#0-2) 

---

### Impact Explanation

Each successful stuffing cycle delays staking of 32 ETH per validator. The ETH is not lost, but it fails to earn EigenLayer restaking yield for the duration of the attack. This matches **Low — Block stuffing** and **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive (requires outbidding all competing transactions for full blocks), making sustained attacks economically irrational for most adversaries. However, the vulnerability is structurally present: the deposit root check creates a race condition that any block-stuffing actor can exploit. The cost scales with block base fee and the number of blocks stuffed, not with the value at risk, so the attack is more plausible during low-fee periods or on L2 forks.

---

### Recommendation

Replace the exact-equality deposit root check with a mechanism that does not create a revert-on-stale-root condition. Two options:

1. **Remove `stake32EthValidated` entirely** and rely solely on `stake32Eth` with offchain monitoring of the pubkey registry to detect front-running. The `expectedDepositRoot` check was added to prevent validator key substitution attacks, but the `IPubkeyRegistry.hasPubkey` guard in `stake32Eth` already prevents pubkey reuse.

2. **Accept a root window**: instead of requiring an exact match, allow the operator to supply a list of acceptable roots (e.g., the last N roots), or check that the deposit count has not advanced beyond a threshold since the operator's snapshot. [4](#0-3) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Fork test outline (Foundry, mainnet fork)
// 1. Deploy / fork NodeDelegator with 32 ETH already in the NDC.
// 2. Snapshot the current deposit root R0 = ethPOS.get_deposit_root().
// 3. Simulate block stuffing: vm.roll(block.number + N) and have a
//    mock ETHPOSDeposit advance its root to R1 != R0.
// 4. Call stake32EthValidated(pubkey, sig, ddRoot, R0).
// 5. Assert the call reverts with InvalidDepositRoot(R0, R1).
// 6. Assert address(nodeDelegator).balance == 32 ether (ETH still idle).
// 7. Assert nodeDelegator.stakedButUnverifiedNativeETH() == 0.

contract BlockStuffingPoC is Test {
    NodeDelegator ndc;
    MockETHPOSDeposit mockDeposit;

    function setUp() public {
        // ... deploy NDC, fund with 32 ETH, wire mock deposit contract
    }

    function testBlockStuffingRevertsStake() public {
        bytes32 r0 = mockDeposit.get_deposit_root();

        // Simulate another deposit advancing the root
        mockDeposit.advanceRoot();
        bytes32 r1 = mockDeposit.get_deposit_root();
        assert(r0 != r1);

        vm.prank(operator);
        vm.expectRevert(
            abi.encodeWithSelector(INodeDelegator.InvalidDepositRoot.selector, r0, r1)
        );
        ndc.stake32EthValidated(pubkey, sig, ddRoot, r0);

        assertEq(address(ndc).balance, 32 ether);
        assertEq(ndc.stakedButUnverifiedNativeETH(), 0);
    }
}
``` [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/NodeDelegator.sol (L159-163)
```text
        IPubkeyRegistry pubkeyRegistry = IPubkeyRegistry(lrtConfig.pubkeyRegistry());
        if (pubkeyRegistry.hasPubkey(pubkey)) {
            revert PubkeyAlreadyRegistered();
        }
        pubkeyRegistry.addPubkey(pubkey);
```

**File:** contracts/NodeDelegator.sol (L164-168)
```text

        // tracks staked but unverified native ETH
        stakedButUnverifiedNativeETH += 32 ether;

        _getEigenPodManager().stake{ value: 32 ether }(pubkey, signature, depositDataRoot);
```

**File:** contracts/NodeDelegator.sol (L186-200)
```text
    function stake32EthValidated(
        bytes calldata pubkey,
        bytes calldata signature,
        bytes32 depositDataRoot,
        bytes32 expectedDepositRoot
    )
        external
    {
        IETHPOSDeposit depositContract = _getEigenPodManager().ethPOS();
        bytes32 actualDepositRoot = depositContract.get_deposit_root();
        if (expectedDepositRoot != actualDepositRoot) {
            revert InvalidDepositRoot(expectedDepositRoot, actualDepositRoot);
        }
        stake32Eth(pubkey, signature, depositDataRoot);
    }
```

**File:** contracts/external/eigenlayer/interfaces/IETHPOSDeposit.sol (L36-38)
```text
    /// @notice Query the current deposit root hash.
    /// @return The deposit root hash.
    function get_deposit_root() external view returns (bytes32);
```

**File:** contracts/interfaces/INodeDelegator.sol (L30-30)
```text
    error InvalidDepositRoot(bytes32 expectedDepositRoot, bytes32 actualDepositRoot);
```
