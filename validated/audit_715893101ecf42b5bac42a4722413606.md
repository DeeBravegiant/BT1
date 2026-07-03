### Title
Collision-Prone UID Generation in `bridgeTokenToL1` Can Cause Bridge Withdrawal Failures and Potential Fund Freeze - (File: contracts/bridges/SonicChainNativeTokenBridge.sol)

---

### Summary

`SonicChainNativeTokenBridge.bridgeTokenToL1` generates a `uint96 uid` for Sonic bridge withdrawals using only block-level and transaction-level parameters with no internal counter. Two calls sharing the same `msg.sender`, `recipient`, `amount`, `tx.gasprice`, and block produce an identical UID. Because `bridgeTokenToL1` has no access control, any external caller can trigger this collision, causing the Sonic bridge to reject the duplicate UID and revert the transaction, or — if the bridge silently accepts duplicates — permanently strand tokens burned on L2 with no corresponding L1 claim.

---

### Finding Description

The UID is computed as:

```solidity
// contracts/bridges/SonicChainNativeTokenBridge.sol  lines 92-100
uint96 uid = uint96(
    uint256(
        keccak256(
            abi.encodePacked(
                block.timestamp, block.number, msg.sender, recipient, amount, tx.gasprice, bridgeReceiver
            )
        )
    ) % type(uint96).max
);
``` [1](#0-0) 

Every input to the hash is either a block-level constant (`block.timestamp`, `block.number`, `bridgeReceiver`) or a transaction parameter that can be identical across two separate transactions (`msg.sender`, `recipient`, `amount`, `tx.gasprice`). There is no monotonically incrementing nonce or counter. Two transactions from the same sender with the same parameters landing in the same block will hash to the same 256-bit value and therefore produce the same truncated `uint96` UID.

The fallback path (triggered when the primary UID is zero) is even weaker — it drops `amount` and `recipient` entirely:

```solidity
// line 104
uid = uint96(uint256(keccak256(abi.encodePacked(block.number, msg.sender, tx.gasprice, bridgeReceiver))));
``` [2](#0-1) 

Additionally, `% type(uint96).max` (i.e., `% (2^96 - 1)`) produces values only in `[0, 2^96 - 2]`; the value `2^96 - 1` is unreachable, which is an off-by-one that further reduces the effective UID space.

The function has **no access control**:

```solidity
// line 73
function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
``` [3](#0-2) 

Any external account can call it directly. The Sonic bridge interface documents `uid` as a "Unique identifier for the withdrawal":

```solidity
// contracts/interfaces/L2/ISonicBridge.sol  line 14
function withdraw(uint96 uid, address token, uint256 amount) external;
``` [4](#0-3) 

The bridge is expected to enforce per-owner UID uniqueness. A duplicate UID from the same owner will either cause a revert (transaction rolls back, tokens returned — DoS) or, if the bridge silently accepts it, burn the tokens on L2 while only one L1 claim is ever processable — permanently freezing the second withdrawal's funds.

---

### Impact Explanation

**Minimum (Medium) — Temporary freezing of funds / failed delivery**: If the Sonic bridge reverts on a duplicate `(owner, uid)` pair, the entire `bridgeTokenToL1` transaction reverts (including the `safeTransferFrom`), so the user's tokens are returned. However, the bridge operation fails silently from the user's perspective, and the user must retry with different parameters or in a different block. Repeated collisions constitute a denial-of-service against the bridge path.

**Maximum (Critical) — Permanent freezing of funds**: If the Sonic bridge accepts the duplicate UID and burns the tokens on L2, only one L1 claim can ever be processed for that UID. The second withdrawal's tokens are permanently unclaimable on L1 — a permanent fund freeze matching the Critical impact tier.

---

### Likelihood Explanation

`bridgeTokenToL1` is callable by any external account with no role restriction. A user who accidentally double-submits a transaction (common with wallet UIs under congestion) with the same `recipient`, `amount`, and `tx.gasprice` in the same block will trigger the collision. The pool contracts (`RSETHPool`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV3ExternalBridge`) call `bridgeTokenToL1` via their `bridgeTokens` functions, which are restricted to `BRIDGER_ROLE`; however, direct calls to `SonicChainNativeTokenBridge` bypass that restriction entirely. Likelihood is **Medium**. [5](#0-4) 

---

### Recommendation

Replace the hash-based UID with an internal monotonically incrementing counter, mirroring the pattern already used in `KernelDepositPool`:

```solidity
// KernelDepositPool.sol line 99 — correct pattern
uint256 public withdrawalCounter;
// ...
uint256 withdrawalId = ++withdrawalCounter;
``` [6](#0-5) 

Apply the same approach in `SonicChainNativeTokenBridge`:

```solidity
uint96 public withdrawalNonce;

function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
    // ...
    uint96 uid = ++withdrawalNonce;
    // ...
}
```

This guarantees a globally unique UID for every call regardless of block, sender, or parameters.

---

### Proof of Concept

1. Alice calls `SonicChainNativeTokenBridge.bridgeTokenToL1(vault, 1000e18)` with `tx.gasprice = 1 gwei` — **Tx A** (nonce 5).
2. Alice, impatient, resubmits the same call — **Tx B** (nonce 6), same parameters, same gas price.
3. Both Tx A and Tx B land in the same block (same `block.timestamp`, `block.number`).
4. Both compute:
   ```
   uid = keccak256(block.timestamp || block.number || Alice || vault || 1000e18 || 1gwei || bridgeReceiver) % type(uint96).max
   ```
   → identical UID `X`.
5. Tx A executes first: `sonicBridge.withdraw(X, token, 1000e18)` succeeds; 1000 tokens burned on L2.
6. Tx B executes: `sonicBridge.withdraw(X, token, 1000e18)` is called with the same UID `X` from the same owner.
   - **If bridge reverts**: Tx B reverts entirely; Alice's tokens are returned. Bridge delivery fails (DoS).
   - **If bridge accepts**: 1000 more tokens are burned on L2. Only one L1 claim for UID `X` exists. Alice permanently loses 1000 tokens. [7](#0-6)

### Citations

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L73-73)
```text
    function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L83-121)
```text
        token.safeTransferFrom(msg.sender, address(this), amount);

        uint256 balance = token.balanceOf(address(this));
        if (balance < amount) revert InsufficientBalance();

        // Get the original token address (validated in constructor)
        address originalToken = tokenPairs.mintedToOriginal(address(token));

        // Generate a unique UID for this transaction
        uint96 uid = uint96(
            uint256(
                keccak256(
                    abi.encodePacked(
                        block.timestamp, block.number, msg.sender, recipient, amount, tx.gasprice, bridgeReceiver
                    )
                )
            ) % type(uint96).max
        );

        // Ensure UID is not zero
        if (uid == 0) {
            uid = uint96(uint256(keccak256(abi.encodePacked(block.number, msg.sender, tx.gasprice, bridgeReceiver))));
        }

        // Store the current token balance before withdrawal
        uint256 balanceBefore = token.balanceOf(address(this));

        // Approve the Sonic bridge to spend the tokens
        token.safeIncreaseAllowance(address(sonicBridge), amount);

        // Initiate withdrawal on Sonic bridge
        // Note: Sonic gateway will only allow SonicBridgeReceiver to claim (same address as this contract)
        sonicBridge.withdraw(uid, originalToken, amount);

        // Verify tokens were transferred/burned
        uint256 balanceAfter = token.balanceOf(address(this));
        if (balanceBefore - balanceAfter != amount) {
            revert BridgeFailed();
        }
```

**File:** contracts/interfaces/L2/ISonicBridge.sol (L10-14)
```text
    /// @notice Initiates a withdrawal from Sonic to Ethereum
    /// @param uid Unique identifier for the withdrawal
    /// @param token The original token address on Ethereum
    /// @param amount The amount to withdraw
    function withdraw(uint96 uid, address token, uint256 amount) external;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L553-576)
```text
    function bridgeTokens(
        address token,
        uint256 amount
    )
        external
        payable
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        if (tokenBridge[token] == address(0)) revert MissingBridgeForToken();
        if (amount == 0) revert InvalidAmount();

        // bridge up to the token balance minus fees
        uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
        if (amount > tokenBalanceMinusFees) revert InsufficientBalance();

        // Approve the required amount to the bridge
        IERC20(token).safeIncreaseAllowance(tokenBridge[token], amount);

        // Call the bridge contract to transfer the tokens
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, amount);

        emit BridgedTokenToL1(token, l1VaultETHForL2Chain, amount);
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L98-100)
```text
    /// @notice A global incremental counter for withdrawal IDs
    uint256 public withdrawalCounter;

```
