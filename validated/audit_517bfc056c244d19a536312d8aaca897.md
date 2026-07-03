### Title
Stale `amount` Parameter in `bridgeAssets()` Allows Block Stuffing to Leave Residual ETH Unbridge — (`contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary

`bridgeAssets()` accepts a caller-supplied `amount` that the bridger quotes off-chain by reading `getETHBalanceMinusFees()`. Because the guard at execution time only checks `balance >= amount` (not `balance == amount`), an attacker can stuff blocks to delay the bridger's transaction while new `deposit()` calls grow the pool balance. When the bridger's transaction finally lands, it bridges only the stale quoted amount, leaving the incremental deposits as residual ETH in the pool until the next bridging cycle.

---

### Finding Description

The bridger's normal workflow is:

1. Read `getETHBalanceMinusFees()` off-chain → value `X`.
2. Submit `bridgeAssets(amount=X, minAmount, nativeFee)`.

The guard inside `bridgeAssets()` is:

```solidity
// line 681
if (getETHBalanceMinusFees() - msg.value < amount) {
    revert InsufficientETHBalance();
}
``` [1](#0-0) 

This only requires `balance ≥ amount`; it does **not** require `amount == balance`. If block stuffing delays the bridger's transaction and users call `deposit()` in the interim, the pool balance grows to `X + Y`. The bridger's transaction passes the guard and bridges only `X`, leaving `Y` ETH in the pool.

Contrast this with `bridgeAssetsViaNativeBridge()`, which reads the live balance at execution time and is therefore immune:

```solidity
// line 657
uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(...);
``` [2](#0-1) 

The `deposit()` function is permissionless and payable, so any user (or the attacker themselves) can grow the pool balance during the stuffing window: [3](#0-2) 

---

### Impact Explanation

The residual `Y` ETH is not permanently lost — it will be bridged in the next cycle — but it is **temporarily frozen on L2**, delaying the backing of rsETH on L1. This matches the allowed scope: **Low. Block stuffing**.

---

### Likelihood Explanation

On L2 networks (Optimism, Arbitrum, etc.) where this contract is deployed, block stuffing is substantially cheaper than on Ethereum mainnet due to low per-gas costs. An attacker can fill blocks for a modest cost to reliably delay the bridger's transaction by several blocks, during which organic or attacker-funded deposits grow the pool balance.

---

### Recommendation

Read `getETHBalanceMinusFees()` at execution time inside `bridgeAssets()`, mirroring the pattern already used in `bridgeAssetsViaNativeBridge()`:

```solidity
function bridgeAssets(uint256 minAmount, uint256 nativeFee) external payable nonReentrant onlyRole(BRIDGER_ROLE) {
    uint256 amount = getETHBalanceMinusFees() - msg.value; // live read
    if (amount == 0) revert InvalidAmount();
    ...
}
```

This eliminates the off-chain quote window entirely and ensures the full non-fee balance is always bridged in one call.

---

### Proof of Concept

```solidity
// Fork test (L2 fork, e.g. Optimism)
function test_blockStuffingLeavesResidualETH() external {
    // 1. Snapshot the current balance
    uint256 balanceBefore = pool.getETHBalanceMinusFees(); // = X

    // 2. Simulate block stuffing: advance blocks, users deposit
    vm.deal(user, 1 ether);
    vm.prank(user);
    pool.deposit{value: 1 ether}("ref"); // pool balance = X + 1 ether

    // 3. Bridger submits with stale amount = X (quoted before stuffing)
    uint256 nativeFee = pool.getNativeFee(balanceBefore, balanceBefore * 99 / 100);
    vm.deal(bridger, nativeFee);
    vm.prank(bridger);
    pool.bridgeAssets{value: nativeFee}(balanceBefore, balanceBefore * 99 / 100, nativeFee);

    // 4. Assert residual ETH remains in pool
    assertGt(pool.getETHBalanceMinusFees(), 0, "residual ETH left unbridge");
}
```

After the call, `getETHBalanceMinusFees() > 0` confirms the invariant is broken for this bridging cycle.

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L681-683)
```text
        if (getETHBalanceMinusFees() - msg.value < amount) {
            revert InsufficientETHBalance();
        }
```
