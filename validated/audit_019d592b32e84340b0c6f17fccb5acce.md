Audit Report

## Title
Stale Caller-Supplied `amount` in `bridgeAssets()` Leaves Residual ETH Unbridge Per Cycle via Block Stuffing — (`contracts/pools/RSETHPoolV3ExternalBridge.sol`)

## Summary
`bridgeAssets()` accepts a caller-supplied `amount` that the bridger quotes off-chain from `getETHBalanceMinusFees()`. The on-chain guard only enforces `balance >= amount`, not `balance == amount`. An attacker can use block stuffing to delay the bridger's transaction while new permissionless `deposit()` calls grow the pool balance; when the bridger's transaction lands it bridges only the stale quoted amount, leaving the incremental deposits as residual ETH in the pool until the next bridging cycle.

## Finding Description
The guard in `bridgeAssets()` at line 681–683 is:

```solidity
if (getETHBalanceMinusFees() - msg.value < amount) {
    revert InsufficientETHBalance();
}
```

This is a lower-bound check only. If the pool balance grows from `X` to `X + Y` between the bridger's off-chain quote and transaction inclusion, the check still passes and only `X` is sent to Stargate via `stargatePool.send{ value: nativeFee + amount }(...)` at line 706. The `Y` increment is silently left in the contract.

`bridgeAssetsViaNativeBridge()` (lines 657–661) is immune because it reads the live balance at execution time and forwards the entire amount:

```solidity
uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(...);
```

The `deposit()` function (lines 366–384) is `external payable` with no access control, so any user or the attacker themselves can grow the pool balance during the stuffing window. The attacker's cost is block stuffing fees on L2 (substantially cheaper than mainnet) plus any self-funded deposits.

## Impact Explanation
The residual `Y` ETH is not permanently lost — it will be bridged in the next cycle — but it is temporarily withheld from L1, delaying the backing of rsETH. This matches the allowed scope: **Low. Block stuffing**.

## Likelihood Explanation
On L2 networks (Optimism, Arbitrum, Base, etc.) where this contract is deployed, block stuffing is substantially cheaper than on Ethereum mainnet due to low per-gas costs. An attacker can fill blocks for a modest cost to reliably delay the bridger's transaction by several blocks. During that window, organic user deposits or attacker-funded deposits grow the pool balance. The attack is repeatable every bridging cycle.

## Recommendation
Remove the caller-supplied `amount` parameter from `bridgeAssets()` and read `getETHBalanceMinusFees()` at execution time, mirroring the pattern already used in `bridgeAssetsViaNativeBridge()`:

```solidity
function bridgeAssets(uint256 minAmount, uint256 nativeFee) external payable nonReentrant onlyRole(BRIDGER_ROLE) {
    uint256 amount = getETHBalanceMinusFees() - msg.value; // live read
    if (amount == 0) revert InvalidAmount();
    if (minAmount > amount || minAmount == 0) revert InvalidMinAmount();
    if (msg.value != nativeFee) revert IncorrectNativeFee();
    // ... rest unchanged
}
```

This eliminates the off-chain quote window entirely and ensures the full non-fee balance is always bridged in one call.

## Proof of Concept

```solidity
// Fork test on an L2 (e.g. Optimism fork)
function test_blockStuffingLeavesResidualETH() external {
    // 1. Bridger quotes balance off-chain
    uint256 quotedAmount = pool.getETHBalanceMinusFees(); // = X

    // 2. Block stuffing window: a user deposits 1 ETH
    vm.deal(user, 1 ether);
    vm.prank(user);
    pool.deposit{value: 1 ether}("ref"); // pool balance = X + 1 ether

    // 3. Bridger submits with stale amount = X
    uint256 nativeFee = pool.getNativeFee(quotedAmount, quotedAmount * 99 / 100);
    vm.deal(bridger, nativeFee);
    vm.prank(bridger);
    pool.bridgeAssets{value: nativeFee}(quotedAmount, quotedAmount * 99 / 100, nativeFee);

    // 4. Residual ETH remains in pool — guard passed, but 1 ether was not bridged
    assertGt(pool.getETHBalanceMinusFees(), 0, "residual ETH left unbridge");
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L657-661)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L670-683)
```text
    function bridgeAssets(
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee
    )
        external
        payable
        nonReentrant
        onlyRole(BRIDGER_ROLE)
    {
        // Exclude msg.value so reserved fees can’t be accidentally consumed
        if (getETHBalanceMinusFees() - msg.value < amount) {
            revert InsufficientETHBalance();
        }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L704-706)
```text

        (MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt) =
            stargatePool.send{ value: nativeFee + amount }(sendParam, fee, msg.sender);
```
