The code confirms all claims. Let me verify the exact behavior:

1. `receive()` at [1](#0-0)  has no access control — any address can send ETH.

2. `depositETHForL1VaultETH` reads `address(this).balance` at [2](#0-1)  and forwards the entire amount including any donated ETH.

3. `getETHDistributionData` reads `address(this).balance` of the deposit pool at [3](#0-2)  — which already includes the just-forwarded ETH since it arrived as `msg.value`.

4. `_checkIfDepositAmountExceedesCurrentLimit` for ETH checks `totalAssetDeposits > limit` (not `totalAssetDeposits + amount > limit`) at [4](#0-3)  — the ETH is already counted in the balance before the check runs.

All four steps of the exploit chain are confirmed in the actual code.

---

Audit Report

## Title
Unprivileged ETH Donation to `L1Vault` Can Temporarily Freeze Bridged ETH via Deposit Limit Exhaustion — (`contracts/L1Vault.sol`)

## Summary
`L1Vault.receive()` accepts ETH from any sender with no access control. `depositETHForL1VaultETH` forwards `address(this).balance` in full to `LRTDepositPool.depositETH`, including any donated ETH. The ETH deposit limit check in `LRTDepositPool` reads `address(this).balance` post-receipt and compares it directly against the limit without adding `amount` separately, so donated ETH inflates the total and can push it over the limit, causing a revert with `MaximumDepositLimitReached` and leaving legitimately bridged ETH frozen in `L1Vault`.

## Finding Description
**Root cause:** Three design choices combine to create the vulnerability:

1. `L1Vault.receive()` (line 368) is unrestricted — any EOA or contract can push ETH into `L1Vault`.

2. `depositETHForL1VaultETH` (lines 150–158) reads `address(this).balance` and sends the entire amount to `lrtDepositPool.depositETH{value: balanceOfETH}(...)`. There is no mechanism to deposit only the legitimately bridged portion.

3. `_checkIfDepositAmountExceedesCurrentLimit` (lines 676–682) handles ETH differently from ERC20s: for ETH it checks `totalAssetDeposits > limit`, where `totalAssetDeposits` is sourced from `getETHDistributionData()` → `address(this).balance` of the deposit pool (line 480). Because the ETH arrives as `msg.value` before the check runs, the donated ETH is already included in `totalAssetDeposits` at check time.

**Exploit path:**
1. Precondition: ETH deposit limit is nearly exhausted (realistic for a live, high-TVL protocol).
2. Attacker calls `address(l1Vault).call{value: X}("")` — succeeds due to open `receive()`.
3. Manager calls `depositETHForL1VaultETH()` — forwards `bridgedETH + donatedETH` to `depositETH`.
4. Inside `depositETH`, `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit`.
5. `getTotalAssetDeposits(ETH)` returns `(pre-existing system ETH) + bridgedETH + donatedETH`.
6. If this sum exceeds the limit, the call reverts with `MaximumDepositLimitReached`.
7. Bridged ETH remains frozen in `L1Vault` until admin raises the deposit limit.
8. Attacker can repeat the donation each time the limit is raised.

**Why existing checks fail:** The `nonReentrant` guard on `depositETHForL1VaultETH` and the `onlyRole(MANAGER_ROLE)` restriction do not prevent the donation step. The deposit limit check itself is the mechanism that is exploited — it is correct in isolation but becomes a griefing vector when combined with the open `receive()` and full-balance forwarding.

## Impact Explanation
Legitimately bridged ETH accumulates in `L1Vault` but cannot be deposited into `LRTDepositPool` to mint rsETH for L2 users. There is no function in `L1Vault` to deposit a partial amount or to expel donated ETH. The freeze persists until the admin raises the deposit limit via a governance/timelock action. This is **Medium — Temporary freezing of funds**, matching the allowed impact scope exactly.

## Likelihood Explanation
- Requires no privileges; any EOA can trigger it via a direct ETH transfer.
- The attacker's ETH is not permanently lost (it is eventually deposited when the limit is raised), making the griefing cost near-zero.
- The precondition (limit nearly exhausted) is realistic for a protocol with significant TVL.
- The attack is repeatable: each time the admin raises the limit, the attacker can donate again to re-exhaust it, making the freeze persistent with minimal ongoing cost.

## Recommendation
1. **Track bridged ETH separately.** Maintain a `pendingBridgedETH` counter incremented in `receive()` only when `msg.sender` is the authorized L2 bridge messenger, and decremented in `depositETHForL1VaultETH`. Deposit only the tracked amount, not `address(this).balance`.

2. **Alternatively**, add a `depositAmount` parameter to `depositETHForL1VaultETH` so the MANAGER can specify exactly how much to deposit, ignoring any surplus balance.

3. **Restrict `receive()`** to only accept ETH from the authorized L2 bridge messenger, reverting for all other senders.

## Proof of Concept
```solidity
// Foundry fork test — unmodified production contracts
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
    assertTrue(ok); // receive() accepts it unconditionally

    // MANAGER attempts to deposit — reverts with MaximumDepositLimitReached
    // because getTotalAssetDeposits(ETH) now includes donated ETH
    vm.prank(manager);
    vm.expectRevert(ILRTDepositPool.MaximumDepositLimitReached.selector);
    l1Vault.depositETHForL1VaultETH();
    // 0.5 ether of bridged ETH is now frozen in L1Vault
}
```

### Citations

**File:** contracts/L1Vault.sol (L150-158)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");
```

**File:** contracts/L1Vault.sol (L367-368)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
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
