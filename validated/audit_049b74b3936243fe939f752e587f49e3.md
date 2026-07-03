### Title
No QoS on Instant Withdrawals Enables Vault Buffer Drain DoS - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

The `instantWithdrawal` function in `LRTWithdrawalManager` has no queue or fairness mechanism. An unprivileged attacker holding rsETH (obtainable by depositing ETH into `LRTDepositPool`) can repeatedly drain the `LRTUnstakingVault`'s available instant-withdrawal balance, causing all other users' `instantWithdrawal` calls to revert. Legitimate users are then forced to fall back to the queued withdrawal path, which imposes an 8-day delay.

---

### Finding Description

`LRTWithdrawalManager.instantWithdrawal` serves instant redemptions by pulling ETH (or LSTs) directly from `LRTUnstakingVault`:

```
// contracts/LRTWithdrawalManager.sol  lines 212-253
function instantWithdrawal(address asset, uint256 rsETHUnstaked, ...) external {
    ...
    uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
    IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
    ILRTUnstakingVault unstakingVault = ...;
    if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
        revert CantInstantWithdrawMoreThanAvailable();
    }
    unstakingVault.redeem(asset, assetAmountUnlocked);
    ...
}
```

The available balance is computed in `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal`:

```
// contracts/LRTUnstakingVault.sol  lines 229-238
function getAssetsAvailableForInstantWithdrawal(address asset) external view returns (uint256 availableAmount) {
    uint256 vaultBalance = balanceOf(asset);
    uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
    availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
}
```

There is no queue, no rate-limit, and no per-user cap on instant withdrawals. The mechanism is purely first-come-first-served. When the available balance reaches zero, every subsequent `instantWithdrawal` call reverts with `CantInstantWithdrawMoreThanAvailable`.

**Attack path:**

1. Attacker calls `LRTDepositPool.depositETH{ value: X }()` → receives Y rsETH proportional to X.
2. Attacker calls `LRTWithdrawalManager.instantWithdrawal(ETH_TOKEN, Y, ...)` → burns Y rsETH, receives ≈ X ETH from `LRTUnstakingVault` (minus `instantWithdrawalFee`).
3. Attacker repeats steps 1–2 until `getAssetsAvailableForInstantWithdrawal` returns 0.

The deposited ETH flows into `LRTDepositPool` (and eventually to EigenLayer via node delegators), while the ETH paid out comes from `LRTUnstakingVault`. These are separate pools; depositing into one does not replenish the other. The vault is only replenished when operators complete EigenLayer unstaking cycles, which takes days.

The `instantWithdrawalFee` is initialized to 0 (Solidity default) and can be set up to 10% by the manager. At 0% fee the attack costs only gas. Even at a non-zero fee the attacker's cost per unit drained is bounded by the fee percentage, making large-scale draining economically viable for a motivated actor.

---

### Impact Explanation

Once the vault's available balance is exhausted:

- Every `instantWithdrawal` call reverts with `CantInstantWithdrawMoreThanAvailable`.
- Users who expected instant liquidity are forced onto the queued withdrawal path (`initiateWithdrawal` → `completeWithdrawal`), which enforces an 8-day delay (`withdrawalDelayBlocks = 8 days / 12 seconds`).
- The vault is only replenished by operator-controlled EigenLayer unstaking completions; users have no self-service remedy.

**Impact: Medium — Temporary freezing of funds** (users' rsETH is not lost, but their ability to access liquidity instantly is blocked for an extended period).

---

### Likelihood Explanation

- The entry path (`depositETH` → `instantWithdrawal`) is fully permissionless and requires no special role.
- The attacker needs ETH capital proportional to the vault's available balance, but recovers most of it (minus fee + gas) each iteration.
- At `instantWithdrawalFee = 0` (the default), the attack is essentially free beyond gas costs.
- The attack can be sustained indefinitely as long as the attacker is willing to pay gas, since the vault replenishment is slow (operator-driven, multi-day EigenLayer cycle).

**Likelihood: Medium** — requires capital and gas expenditure, but no privileged access and no complex exploit chain.

---

### Recommendation

1. **Implement a per-block or per-user rate limit** on `instantWithdrawal` to prevent a single address from draining the vault in a short window.
2. **Enforce a non-zero minimum `instantWithdrawalFee`** to raise the economic cost of repeated draining.
3. **Consider a withdrawal queue** for instant withdrawals analogous to the queued path: if the vault balance is insufficient, place the request in a short-priority queue rather than reverting, so legitimate users are not forced to retry competitively.
4. **Add a cooldown** between successive `instantWithdrawal` calls from the same address.

---

### Proof of Concept

```
// Pseudocode — no test harness required
address attacker = address(0xBEEF);

// Step 1: Attacker acquires rsETH
LRTDepositPool.depositETH{value: vaultAvailableBalance}(""); 
// attacker now holds rsETH ≈ vaultAvailableBalance / rsETHPrice

// Step 2: Drain the vault
uint256 rsETHBalance = rsETH.balanceOf(attacker);
LRTWithdrawalManager.instantWithdrawal(ETH_TOKEN, rsETHBalance, "");
// vault.getAssetsAvailableForInstantWithdrawal(ETH_TOKEN) == 0 after this call

// Step 3: Legitimate user is blocked
// vm.prank(victim);
LRTWithdrawalManager.instantWithdrawal(ETH_TOKEN, victimRsETH, "");
// reverts: CantInstantWithdrawMoreThanAvailable
```

**Root cause references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L56-56)
```text
    uint256 public instantWithdrawalFee; // Fee in basis points (1 = 0.01%)
```

**File:** contracts/LRTWithdrawalManager.sol (L228-235)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTUnstakingVault.sol (L229-238)
```text
    function getAssetsAvailableForInstantWithdrawal(address asset)
        external
        view
        onlySupportedAsset(asset)
        returns (uint256 availableAmount)
    {
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
    }
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
