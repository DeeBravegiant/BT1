I need to carefully trace the deposit limit check for ETH to determine if this is a real vulnerability.

### Title
Unprivileged ETH Donation to `L1Vault` Can Permanently Block `depositETHForL1VaultETH` via Deposit Limit Exhaustion — (`contracts/L1Vault.sol`)

---

### Summary

Any unprivileged actor can send ETH directly to `L1Vault` via its open `receive()` function. Because `depositETHForL1VaultETH` always deposits `address(this).balance` in full, the donated ETH inflates the amount sent to `LRTDepositPool.depositETH`. The ETH deposit limit check in `LRTDepositPool` operates on the post-receipt balance of the pool, so the donated ETH is counted against the limit. When the limit is nearly exhausted, a small donation causes the check to exceed the limit and revert with `MaximumDepositLimitReached`, temporarily freezing all legitimately bridged ETH inside `L1Vault`.

---

### Finding Description

**Step 1 — Open `receive()` in L1Vault**

`L1Vault` accepts ETH from any sender with no access control: [1](#0-0) 

**Step 2 — `depositETHForL1VaultETH` uses full balance**

The function reads `address(this).balance`, which includes any donated ETH, and forwards the entire amount to `LRTDepositPool.depositETH`: [2](#0-1) 

**Step 3 — ETH limit check runs after ETH is already received**

Inside `LRTDepositPool.depositETH`, `_beforeDeposit` is called. The limit check for ETH reads `address(this).balance` of the deposit pool — which already includes the just-forwarded ETH (sent as `msg.value`): [3](#0-2) [4](#0-3) 

**Step 4 — The ETH-specific limit check has no `amount` parameter**

For ERC20 assets the check is `totalAssetDeposits + amount > limit`. For ETH it is simply `totalAssetDeposits > limit`, because the ETH is already in the pool's balance: [5](#0-4) 

This means: if `(pre-existing system ETH) + (bridged ETH) + (donated ETH) > limit`, the call reverts with `MaximumDepositLimitReached`. [6](#0-5) 

---

### Impact Explanation

Legitimately bridged ETH accumulates in `L1Vault` but cannot be deposited into `LRTDepositPool` to mint rsETH. There is no function in `L1Vault` to deposit a partial amount or to withdraw donated ETH. The only resolution is for the admin to raise the deposit limit. Until then, all bridged ETH is frozen inside `L1Vault`. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

- The attack requires no privileges; any EOA can call `L1Vault.receive()`.
- The cost is only the donated ETH plus gas. The attacker does not lose the ETH permanently (it eventually gets deposited when the limit is raised), but the griefing cost is near-zero.
- The precondition (ETH deposit limit nearly exhausted) is realistic for a live, high-TVL protocol.
- The attacker can repeat the donation every time the admin raises the limit, making the freeze persistent with minimal ongoing cost.

---

### Recommendation

1. **Snapshot the balance before accepting the deposit.** Track the amount of ETH received from the L2 bridge separately (e.g., a `pendingBridgedETH` counter incremented in `receive()` only when `msg.sender` is the authorized bridge, and decremented in `depositETHForL1VaultETH`). Deposit only the tracked amount, not `address(this).balance`.

2. **Alternatively**, add a `depositAmount` parameter to `depositETHForL1VaultETH` so the MANAGER can specify exactly how much to deposit, ignoring any surplus balance.

3. **Restrict `receive()`** to only accept ETH from the authorized L2 bridge messenger, rejecting arbitrary donations.

---

### Proof of Concept

```solidity
// Fork test (Foundry) — unmodified production contracts
function test_donationBlocksDepositETHForL1VaultETH() public {
    // Setup: ETH deposit limit = current total deposits + 1 ether
    uint256 currentDeposits = lrtDepositPool.getTotalAssetDeposits(ETH_IDENTIFIER);
    vm.prank(admin);
    lrtConfig.setDepositLimitByAsset(ETH_IDENTIFIER, currentDeposits + 1 ether);

    // Simulate 0.5 ether of legitimately bridged ETH arriving in L1Vault
    vm.deal(address(l1Vault), 0.5 ether);

    // Attacker donates 1.5 ether directly to L1Vault (pushes total over limit)
    address attacker = makeAddr("attacker");
    vm.deal(attacker, 2 ether);
    vm.prank(attacker);
    (bool ok,) = address(l1Vault).call{value: 1.5 ether}("");
    assertTrue(ok);

    // MANAGER attempts to deposit — reverts with MaximumDepositLimitReached
    vm.prank(manager);
    vm.expectRevert(ILRTDepositPool.MaximumDepositLimitReached.selector);
    l1Vault.depositETHForL1VaultETH();
}
```

The 0.5 ether of bridged ETH is now frozen in `L1Vault` until the admin raises the deposit limit.

### Citations

**File:** contracts/L1Vault.sol (L150-161)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
    }
```

**File:** contracts/L1Vault.sol (L367-368)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
