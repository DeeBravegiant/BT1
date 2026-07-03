### Title
`lastBridgedDepositId` Advances Unconditionally Regardless of Bridged Amount — (`contracts/KERNEL/KernelVaultETH.sol`)

### Summary

`bridgeKernelToBSC` sets `lastBridgedDepositId = counter - 1` unconditionally before calling the bridge, with no validation that the `amount` parameter equals the cumulative sum of all unbridged deposits. An operator can bridge a partial amount while the pointer advances to mark all pending deposits as bridged.

### Finding Description

In `_depositKernel`, each deposit is assigned a sequential ID from `counter` and stored in `userDeposits`: [1](#0-0) 

In `bridgeKernelToBSC`, the only balance check is that the contract holds at least `amount` tokens: [2](#0-1) 

Then, before the bridge call, the pointer is unconditionally advanced: [3](#0-2) 

There is no check that `amount` equals the sum of `userDeposits[lastBridgedDepositId+1].amount` through `userDeposits[counter-1].amount`. The operator can supply any `amount` that satisfies `kernel.balanceOf(address(this)) >= amount`, and `lastBridgedDepositId` will still jump to `counter - 1`.

### Impact Explanation

Users whose deposits were not included in the bridged batch have their deposit IDs implicitly marked as "bridged" (i.e., `depositId <= lastBridgedDepositId`) while their tokens remain in the vault and are never restaked on BSC. Off-chain systems or UIs that use `lastBridgedDepositId` as the source of truth for restaking status will report incorrect state. The tokens are not lost, but the promised restaking yield is not delivered for those deposits until the accounting is manually corrected — matching the **Low: contract fails to deliver promised returns, but doesn't lose value** scope.

### Likelihood Explanation

The operator role is trusted, but the code provides no guard against partial bridging. A realistic trigger is an operator batching bridge calls (e.g., due to LayerZero message-size or fee constraints) and supplying an `amount` less than the full vault balance. No malicious intent is required; a miscalculation or intentional batching is sufficient.

### Recommendation

Before setting `lastBridgedDepositId`, compute the exact sum of deposits in the range `(lastBridgedDepositId, counter)` and require that `amount` equals that sum:

```solidity
uint256 expectedAmount = 0;
for (uint256 i = lastBridgedDepositId + 1; i < counter; i++) {
    expectedAmount += userDeposits[i].amount;
}
require(amount == expectedAmount, "Amount mismatch");
lastBridgedDepositId = counter - 1;
```

Alternatively, remove the `amount` parameter entirely and derive it from the deposit records, ensuring the pointer and the bridged value are always consistent.

### Proof of Concept

```solidity
// Setup: 3 users each deposit 100e18 KERNEL
// counter = 3, lastBridgedDepositId = 0 (initial)

// Operator bridges only 100e18 (1/3 of total 300e18)
vault.bridgeKernelToBSC(100e18, 99e18, nativeFee, refundAddr);

// After the call:
// lastBridgedDepositId == 2  (counter - 1 = 3 - 1)
// Only 100e18 was bridged; deposits 1 and 2 (200e18 total) remain in vault
// userDeposits[1] and userDeposits[2] are implicitly considered "bridged"
// Users 1 and 2 are not restaked on BSC despite their tokens being held in the vault
assert(vault.lastBridgedDepositId() == 2);
assert(kernel.balanceOf(address(vault)) == 200e18); // tokens still in vault
``` [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelVaultETH.sol (L225-267)
```text
    function bridgeKernelToBSC(
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee,
        address refundAddress
    )
        external
        payable
        nonReentrant
        onlyRole(OPERATOR_ROLE)
    {
        UtilLib.checkNonZeroAddress(refundAddress);

        if (kernel.balanceOf(address(this)) < amount) {
            revert InsufficientKernelBalance();
        }

        if (minAmount > amount || minAmount == 0) {
            revert InvalidMinAmount();
        }

        if (msg.value < nativeFee) {
            revert InsufficientNativeFee();
        }

        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(),
            amountLD: amount,
            minAmountLD: minAmount,
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = MessagingFee({ nativeFee: nativeFee, lzTokenFee: 0 });

        lastBridgedDepositId = counter - 1;

        kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);

        emit BridgedKernelToBSC(dstLzChainId, receiver, amount, minAmount, nativeFee, lastBridgedDepositId);
    }
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L391-394)
```text
        uint256 depositId = counter;

        userDeposits[depositId] = UserDeposit({ user: user, amount: amount });
        ++counter;
```
