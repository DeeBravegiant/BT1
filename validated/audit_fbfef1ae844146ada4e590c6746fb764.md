The contract code is fully readable. Let me analyze the exact mechanics.

**Key lines:**

- `_depositKernel`: assigns `depositId = counter`, stores deposit, then `++counter` [1](#0-0) 
- `bridgeKernelToBSC`: sets `lastBridgedDepositId = counter - 1` unconditionally at execution time, then bridges `amount` [2](#0-1) 

The only balance guard is `kernel.balanceOf(address(this)) < amount` — it checks the vault holds enough tokens, but does **not** verify that `amount` equals the sum of deposits `[lastBridgedDepositId+1 .. counter-1]`. [3](#0-2) 

---

### Title
TOCTOU Race Condition in `bridgeKernelToBSC` Advances `lastBridgedDepositId` Past Unbridged Deposits — (`contracts/KERNEL/KernelVaultETH.sol`)

### Summary
`bridgeKernelToBSC` sets `lastBridgedDepositId = counter - 1` using the **on-chain `counter` at execution time**, not the counter value the operator observed off-chain when computing `amount`. A deposit that lands between the operator's off-chain read and the transaction's on-chain execution is silently marked as bridged while its tokens remain in the vault permanently.

### Finding Description
The operator's off-chain workflow is:
1. Read `counter = N` → compute `amount = sum(userDeposits[0..N-1].amount)`.
2. Submit `bridgeKernelToBSC(amount, ...)`.

Between steps 1 and 2, a user calls `depositKernel`, which increments `counter` to `N+1` and records `userDeposits[N]`.

When the operator's transaction executes:
- `lastBridgedDepositId = counter - 1 = N` [4](#0-3) 
- `amount` bridged covers only deposits `0..N-1`
- Deposit `N`'s tokens remain in the vault

The watermark now claims deposit `N` was bridged. The off-chain system, on its next cycle, reads `lastBridgedDepositId = N` and `counter = N+1`, computes zero new deposits to bridge, and deposit `N`'s tokens are permanently stranded. There is no on-chain recovery path — no rescue function, no way to re-bridge a deposit whose ID is already behind the watermark.

### Impact Explanation
Deposit `N`'s KERNEL tokens are permanently locked in `KernelVaultETH` on Ethereum mainnet. They are never bridged to BSC and never restaked in Kernel Protocol. The user's yield-generating position is never established, constituting **permanent freezing of unclaimed yield** (Medium scope).

### Likelihood Explanation
This is a standard TOCTOU condition on any live network. Ethereum block times are ~12 seconds; the operator's off-chain read-to-submission window routinely spans multiple blocks. Any user deposit that is mined in that window triggers the bug. No attacker coordination is required — a normal user deposit is sufficient. The operator acts in good faith throughout.

### Recommendation
Pass the expected upper deposit ID as a parameter and assert it matches `counter - 1` at execution time:

```solidity
function bridgeKernelToBSC(
    uint256 amount,
    uint256 minAmount,
    uint256 nativeFee,
    address refundAddress,
    uint256 expectedLastDepositId   // <-- new parameter
) external payable nonReentrant onlyRole(OPERATOR_ROLE) {
    require(expectedLastDepositId == counter - 1, "counter mismatch");
    lastBridgedDepositId = expectedLastDepositId;
    // ... rest unchanged
}
```

This makes the transaction revert (rather than silently mismatch) if a new deposit lands before execution, allowing the operator to retry with the correct amount.

### Proof of Concept

```solidity
// 1. Alice deposits 100 KERNEL → depositId=0, counter=1
vault.depositKernel(100e18);          // Alice

// 2. Operator reads counter=1, computes amount=100e18, submits tx

// 3. Bob deposits 50 KERNEL → depositId=1, counter=2  (lands first)
vault.depositKernel(50e18);           // Bob

// 4. Operator's tx executes:
//    lastBridgedDepositId = counter-1 = 1
//    bridges only 100e18 (Alice's amount)
//    Bob's 50e18 stays in vault
vault.bridgeKernelToBSC{value: fee}(100e18, 99e18, fee, refund);

// 5. Assert invariant broken:
assert(lastBridgedDepositId == 1);    // watermark says Bob was bridged
assert(kernel.balanceOf(vault) >= 50e18); // but Bob's tokens are still here

// 6. Next operator cycle: lastBridgedDepositId=1, counter=2 → no new deposits → Bob never bridged
```

### Citations

**File:** contracts/KERNEL/KernelVaultETH.sol (L238-240)
```text
        if (kernel.balanceOf(address(this)) < amount) {
            revert InsufficientKernelBalance();
        }
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L262-264)
```text
        lastBridgedDepositId = counter - 1;

        kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L391-394)
```text
        uint256 depositId = counter;

        userDeposits[depositId] = UserDeposit({ user: user, amount: amount });
        ++counter;
```
