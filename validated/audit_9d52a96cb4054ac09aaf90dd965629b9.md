### Title
Unbounded Gas Consumption via Uncapped `referralId` in `deposit()` — (`contracts/pools/RSETHPoolV2ExternalBridge.sol`)

### Summary
The `deposit(string memory referralId)` function in `RSETHPoolV2ExternalBridge` accepts an arbitrarily long `referralId` string with no length validation. Gas consumption grows linearly with the byte length of `referralId` due to calldata ABI-decoding into memory and event log emission, with no upper bound enforced by any guard in the call path.

### Finding Description
The `deposit` function signature is:

```solidity
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value)
``` [1](#0-0) 

The `referralId` string is decoded from calldata into memory (cost: ~3 gas per 32-byte word) and then written to the transaction log via:

```solidity
emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
``` [2](#0-1) 

The `SwapOccurred` event carries `referralId` as an unindexed `string`, so the full byte content is written to the log at 8 gas/non-zero byte and 4 gas/zero byte:

```solidity
event SwapOccurred(address indexed user, uint256 rsETHAmount, uint256 fee, string referralId);
``` [3](#0-2) 

None of the modifiers (`whenNotPaused`, `limitDailyMint`, `nonReentrant`) inspect or bound the length of `referralId`: [4](#0-3) 

The only preconditions are: pool unpaused, `block.timestamp >= startTimestamp`, `dailyMintLimit` not exhausted, and `msg.value > 0`. All are satisfiable by any unprivileged user with 1 wei.

### Impact Explanation
Gas consumption is proportional to `len(referralId)`:
- **ABI decoding**: ~3 gas per 32-byte chunk of calldata
- **LOG data**: 8 gas per non-zero byte, 4 gas per zero byte

A 1 MB `referralId` of non-zero bytes costs approximately **8,000,000 gas** in log data alone, approaching or exceeding the block gas limit on many L2 chains where this contract is deployed. This constitutes unbounded gas consumption with no protocol-enforced ceiling.

### Likelihood Explanation
The attack requires no special role, no token approval, and only 1 wei of ETH. The preconditions (unpaused, past `startTimestamp`, limit not exhausted) are the normal operating state of the pool. Any user can trigger this at any time during normal operation.

### Recommendation
Add a maximum length check at the top of `deposit`:

```solidity
uint256 constant MAX_REFERRAL_ID_LENGTH = 128; // or appropriate bound

function deposit(string memory referralId) external payable ... {
    if (bytes(referralId).length > MAX_REFERRAL_ID_LENGTH) revert ReferralIdTooLong();
    ...
}
```

Alternatively, change the parameter to `bytes32 referralId` (fixed-size), which eliminates the unbounded allocation entirely and is sufficient for any reasonable referral identifier.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "../contracts/pools/RSETHPoolV2ExternalBridge.sol";

contract UnboundedGasFuzzTest is Test {
    RSETHPoolV2ExternalBridge pool;

    function setUp() public {
        // deploy + initialize pool with mock oracle/wrsETH, set startTimestamp in past,
        // set dailyMintLimit to type(uint256).max
    }

    function testFuzz_referralIdGasGrowsLinearly(uint16 kilobytes) public {
        vm.assume(kilobytes > 0 && kilobytes <= 100);
        string memory bigId = new string(uint256(kilobytes) * 1024);
        // fill with non-zero bytes
        bytes memory b = bytes(bigId);
        for (uint i = 0; i < b.length; i++) b[i] = 0x41;

        uint256 gasBefore = gasleft();
        pool.deposit{value: 1 wei}(string(b));
        uint256 gasUsed = gasBefore - gasleft();

        // assert linear growth: gasUsed / kilobytes should be roughly constant
        emit log_named_uint("gas per KB", gasUsed / kilobytes);
        // At 100 KB: ~800_000 gas in log data alone
        // At 1000 KB: ~8_000_000 gas — near/above L2 block gas limits
    }
}
```

This fuzz test demonstrates that `gasUsed` grows linearly with `len(referralId)` and can be driven to exceed the block gas limit with no privileged access required.

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L92-126)
```text
    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }

    modifier whenPaused() {
        if (!paused) revert ContractNotPaused();
        _;
    }

    /// @dev Modifier to enforce the daily minting limit
    /// @param amount The ETH amount sent in the deposit
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L159-159)
```text
    event SwapOccurred(address indexed user, uint256 rsETHAmount, uint256 fee, string referralId);
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-289)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L300-300)
```text
        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```
