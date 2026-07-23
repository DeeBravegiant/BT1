The key code is all present. Let me trace the exact execution path.

The execution path is fully traceable. Here is the complete analysis:

---

### Title
ETH Sent With Non-WETH `exactInputSingle` Is Silently Stranded and Stealable via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`exactInputSingle` (and all other `payable` swap entry points) accept `msg.value` unconditionally. When `tokenIn != WETH`, the `pay()` function falls into the `else` branch and calls `safeTransferFrom`, completely ignoring the router's native ETH balance. That ETH is silently stranded on the router. Because `refundETH()` has no access control and sends `address(this).balance` to `msg.sender`, any third party can call it in a subsequent transaction and claim the stranded ETH.

### Finding Description

**Entry point — `exactInputSingle` is `payable` with no ETH guard:** [1](#0-0) 

The function signature is `external payable` and there is no check that `msg.value == 0` when `params.tokenIn != WETH`. ETH sent here is credited to `address(this).balance`.

**`pay()` silently ignores native ETH for non-WETH tokens:** [2](#0-1) 

When `token != WETH` and `payer != address(this)`, the function executes only `safeTransferFrom(payer, recipient, value)`. The native balance is never read, wrapped, or returned. The ETH from `msg.value` remains on the router with no attribution.

**`refundETH()` sends the entire balance to any caller:** [3](#0-2) 

There is no check that `msg.sender` is the address that originally contributed the ETH. Any EOA or contract can call this function and receive the full `address(this).balance`.

**`receive()` does not prevent ETH from arriving via payable calls:** [4](#0-3) 

The `NotWETH` guard only applies to plain ETH transfers (no calldata). It does not block `msg.value` attached to a payable function call such as `exactInputSingle`.

### Impact Explanation

Direct loss of user principal. User A's ETH is permanently stranded on the router after the swap completes. Any address that calls `refundETH()` before User A does receives the full balance. The loss is 1:1 with the ETH User A mistakenly attached to the call.

### Likelihood Explanation

Moderate. The intended usage pattern (documented in tests) is `multicall{value}([exactInputSingle(WETH, ...), refundETH()])`. A user who calls `exactInputSingle` directly with `msg.value > 0` and a non-WETH `tokenIn` — a plausible mistake when adapting a WETH swap — loses their ETH. A front-running bot monitoring the mempool for stranded router balances can reliably extract it.

### Recommendation

Add a guard in `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` that reverts when `msg.value > 0` and the first `tokenIn` is not `WETH`. Alternatively, add an unconditional `if (address(this).balance > 0) _transferETH(msg.sender, address(this).balance)` at the end of each swap entry point, mirroring the pattern already used in the test suite's `refundETH` multicall step.

### Proof of Concept

```solidity
// Foundry test sketch
function test_strandedEthStolenByThirdParty() public {
    address userA = makeAddr("userA");
    address userB = makeAddr("userB");
    vm.deal(userA, 1 ether);

    // userA swaps token1 -> token2 (neither is WETH) but accidentally attaches 1 ETH
    vm.prank(userA);
    router.exactInputSingle{value: 1 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool12),
            tokenIn: address(token1),   // NOT WETH
            tokenOut: address(token2),
            zeroForOne: true,
            amountIn: 1_000,
            amountOutMinimum: 0,
            recipient: userA,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // 1 ETH is now stranded on the router; userA's swap settled via safeTransferFrom

    assertEq(address(router).balance, 1 ether); // ETH stranded

    // userB steals it in a separate transaction
    vm.prank(userB);
    router.refundETH();

    assertEq(userB.balance, 1 ether);  // userB received userA's ETH
    assertEq(userA.balance, 0);        // userA lost 1 ETH
}
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
