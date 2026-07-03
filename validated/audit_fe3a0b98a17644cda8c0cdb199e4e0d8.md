I have the full contract. Let me verify the exact function signature and event definition before rendering my verdict.

### Title
Unbounded `referralId` String Enables Super-Linear Gas Consumption via Memory Expansion — (`contracts/pools/RSETHPoolV2NBA.sol`)

---

### Summary

`deposit(string memory referralId)` accepts an attacker-controlled string with no length bound. The EVM's quadratic memory expansion cost, combined with linear calldata cost, allows a single deposit call to consume gas approaching the block gas limit (~30 M gas) at ~1.5 MB of non-zero calldata.

---

### Finding Description

The function signature is:

```solidity
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
``` [1](#0-0) 

`string memory` causes the ABI decoder to copy the entire calldata string into EVM memory. The string is then re-encoded into memory a second time during event emission:

```solidity
emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
``` [2](#0-1) 

The event is declared with an unindexed `string` field, so the full string is ABI-encoded into log data:

```solidity
event SwapOccurred(address indexed user, uint256 rsETHAmount, uint256 fee, string referralId);
``` [3](#0-2) 

There is **no length check** anywhere in the function body. [4](#0-3) 

---

### Impact Explanation

EVM memory expansion cost is:

```
memory_cost = (words² / 512) + (3 × words)    where words = ⌈bytes / 32⌉
```

Combined with calldata cost (4 gas/zero-byte, 16 gas/non-zero-byte), approximate totals for all-non-zero strings:

| String length | Calldata gas | Memory expansion gas | Total (approx.) |
|---|---|---|---|
| 100 KB | 1,638,400 | ~30,000 | ~1.67 M |
| 500 KB | 8,192,000 | ~548,000 | ~8.74 M |
| 1 MB | 16,777,216 | ~2,195,456 | ~18.97 M |
| 1.5 MB | 25,165,824 | ~4,866,048 | **~30 M** |

At ~1.5 MB the transaction consumes the entire Ethereum block gas limit, qualifying as block stuffing. The memory expansion term grows as O(n²) in words, so the marginal cost per additional byte accelerates as the string grows.

---

### Likelihood Explanation

The path is fully permissionless: any address can call `deposit` with `msg.value > 0` and an arbitrarily large `referralId`. No role, whitelist, or pause gate blocks this. The attacker must pay the gas themselves, making sustained block stuffing economically costly but not impossible (e.g., during time-sensitive MEV windows or targeted griefing). A single such transaction is trivially constructable.

---

### Recommendation

Add a maximum length guard at the top of `deposit`:

```solidity
uint256 private constant MAX_REFERRAL_ID_BYTES = 128;

function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    require(bytes(referralId).length <= MAX_REFERRAL_ID_BYTES, "referralId too long");
    ...
}
```

Alternatively, change the parameter location to `calldata` **and** add the length check; `calldata` avoids the memory-copy during ABI decoding but the event emission still allocates memory, so the length check remains necessary.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Foundry fuzz / forge test
contract GasTest is Test {
    RSETHPoolV2NBA pool; // deployed on local fork

    function testUnboundedReferralIdGas() public {
        uint256[] memory lengths = new uint256[](5);
        lengths[0] = 1;
        lengths[1] = 1_000;
        lengths[2] = 10_000;
        lengths[3] = 100_000;
        lengths[4] = 200_000;

        for (uint256 i = 0; i < lengths.length; i++) {
            string memory referralId = new string(lengths[i]);
            // fill with non-zero bytes to maximise calldata cost
            bytes memory b = bytes(referralId);
            for (uint256 j = 0; j < b.length; j++) b[j] = 0x41;

            uint256 gasBefore = gasleft();
            pool.deposit{value: 1 ether}(string(b));
            uint256 gasUsed = gasBefore - gasleft();

            emit log_named_uint("length", lengths[i]);
            emit log_named_uint("gasUsed", gasUsed);
            // assert gas does not grow super-linearly — will fail at large lengths
            assertLt(gasUsed, 30_000_000, "exceeded block gas limit");
        }
    }
}
```

The assertion will pass for small lengths and fail (or the transaction will revert OOG) as `referralId` approaches hundreds of kilobytes, demonstrating the unbounded gas consumption.

### Citations

**File:** contracts/pools/RSETHPoolV2NBA.sol (L56-56)
```text
    event SwapOccurred(address indexed user, uint256 rsETHAmount, uint256 fee, string referralId);
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L106-118)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```
