Audit Report

## Title
Stale Caller-Supplied `amount` in `bridgeAssets()` Allows Block Stuffing to Leave Residual ETH Unbridged — (`contracts/pools/RSETHPoolV3ExternalBridge.sol`)

## Summary

`bridgeAssets()` accepts a caller-supplied `amount` that the bridger quotes off-chain via `getETHBalanceMinusFees()`. The on-chain guard only enforces `balance >= amount`, not `balance == amount`. An attacker can use block stuffing on a cheap L2 to delay the bridger's transaction while permissionless `deposit()` calls grow the pool balance, causing the bridger to bridge only the stale quoted amount and leaving the incremental deposits as residual ETH in the pool until the next bridging cycle.

## Finding Description

The `bridgeAssets()` function signature accepts `amount` as a parameter supplied by the caller at submission time: [1](#0-0) 

The guard at execution time is: [2](#0-1) 

This check only requires `balance >= amount`. It does not enforce `amount == balance`. If new deposits arrive between the off-chain quote and transaction execution, the pool balance grows to `X + Y`, the guard passes, and only `X` is bridged via Stargate: [3](#0-2) 

The residual `Y` remains in the contract. By contrast, `bridgeAssetsViaNativeBridge()` reads the live balance at execution time and is immune: [4](#0-3) 

The permissionless `deposit()` function is the entry point that grows the balance during the stuffing window: [5](#0-4) 

`getETHBalanceMinusFees()` is a simple live balance read (`address(this).balance - feeEarnedInETH`), so any ETH deposited after the off-chain quote but before the bridger's transaction lands is invisible to the stale `amount`: [6](#0-5) 

**Exploit flow:**
1. Bridger reads `getETHBalanceMinusFees()` off-chain → value `X`.
2. Attacker stuffs blocks on L2 to delay the bridger's pending transaction.
3. During the delay, attacker or organic users call `deposit()`, growing the pool to `X + Y`.
4. Bridger's transaction lands; guard passes (`X + Y >= X`); only `X` is bridged.
5. `Y` ETH remains in the pool until the next bridging cycle.

## Impact Explanation

The residual `Y` ETH is not permanently lost but is temporarily frozen on L2, delaying the backing of rsETH on L1 for one bridging cycle. This concretely matches the allowed impact class **Low. Block stuffing**: an attacker deliberately uses block stuffing as the mechanism to cause the protocol to fail to bridge its full balance in a given cycle.

## Likelihood Explanation

On the L2 networks where `RSETHPoolV3ExternalBridge` is deployed (Base, Linea), per-gas costs are orders of magnitude lower than Ethereum mainnet, making block stuffing economically feasible for a modest attacker budget. The attack is repeatable every bridging cycle. No privileged access is required; the attacker only needs to submit filler transactions and optionally call the permissionless `deposit()`.

## Recommendation

Remove the `amount` parameter from `bridgeAssets()` and read the live balance at execution time, mirroring the pattern already used in `bridgeAssetsViaNativeBridge()`:

```solidity
function bridgeAssets(uint256 minAmount, uint256 nativeFee)
    external payable nonReentrant onlyRole(BRIDGER_ROLE)
{
    uint256 amount = getETHBalanceMinusFees() - msg.value; // live read
    if (amount == 0) revert InvalidAmount();
    if (minAmount > amount || minAmount == 0) revert InvalidMinAmount();
    if (msg.value != nativeFee) revert IncorrectNativeFee();
    // ... rest of bridging logic unchanged
}
```

This eliminates the off-chain quote window entirely and ensures the full non-fee balance is always bridged in one call, consistent with `bridgeAssetsViaNativeBridge()`.

## Proof of Concept

```solidity
// Fork test on an L2 fork (e.g., Base)
function test_blockStuffingLeavesResidualETH() external {
    // 1. Snapshot the current bridgeable balance
    uint256 balanceBefore = pool.getETHBalanceMinusFees(); // = X

    // 2. Simulate block stuffing window: a user deposits
    vm.deal(user, 1 ether);
    vm.prank(user);
    pool.deposit{value: 1 ether}("ref"); // pool balance = X + 1 ether

    // 3. Bridger submits with stale amount = X (quoted before stuffing)
    uint256 nativeFee = pool.getNativeFee(balanceBefore, balanceBefore * 99 / 100);
    vm.deal(bridger, nativeFee);
    vm.prank(bridger);
    pool.bridgeAssets{value: nativeFee}(
        balanceBefore,
        balanceBefore * 99 / 100,
        nativeFee
    );

    // 4. Assert residual ETH remains in pool
    assertGt(pool.getETHBalanceMinusFees(), 0, "residual ETH left unbridged");
}
```

After the call, `getETHBalanceMinusFees() > 0` confirms the 1 ETH deposited during the stuffing window was not bridged in this cycle.

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-384)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L493-495)
```text
    function getETHBalanceMinusFees() public view returns (uint256) {
        return address(this).balance - feeEarnedInETH;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L657-661)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L670-673)
```text
    function bridgeAssets(
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L681-683)
```text
        if (getETHBalanceMinusFees() - msg.value < amount) {
            revert InsufficientETHBalance();
        }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L705-706)
```text
        (MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt) =
            stargatePool.send{ value: nativeFee + amount }(sendParam, fee, msg.sender);
```
