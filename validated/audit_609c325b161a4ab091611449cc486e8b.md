### Title
Pool Contracts Lack `receive()` Function, Causing ETH Refund Failures from Token Bridge Contracts - (File: contracts/pools/RSETHPool.sol)

### Summary

Multiple L2 pool contracts (`RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) forward native ETH to external token bridge contracts via `bridgeTokens()`, but none of these pool contracts implement a `receive()` or `fallback()` function. If the token bridge contract attempts to refund excess ETH back to the pool — a standard pattern in bridge contracts — the refund call will revert, causing the entire `bridgeTokens()` transaction to fail and preventing token assets from being moved to L1.

---

### Finding Description

The `bridgeTokens()` function in each affected pool contract sends `msg.value` (native ETH) to an external `IL2TokenBridge` contract as a fee for bridging tokens to L1:

**RSETHPool.sol:** [1](#0-0) 

**RSETHPoolNoWrapper.sol:** [2](#0-1) 

**RSETHPoolV3ExternalBridge.sol:** [3](#0-2) 

**RSETHPoolV3WithNativeChainBridge.sol:** [4](#0-3) 

In all four cases, the pool contract is the `msg.sender` to the bridge. If the bridge contract refunds any unused ETH back to its caller (the pool), the pool has no `receive()` or `fallback()` function to accept it, causing the entire transaction to revert.

None of the affected contracts define a `receive()` function. Contrast this with `L1Vault.sol` and `L1VaultV2.sol`, which correctly implement `receive() external payable {}` precisely because they are expected to receive ETH from external contracts: [5](#0-4) [6](#0-5) 

The pool contracts have no such provision.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds.**

The `bridgeTokens()` function is the only mechanism for moving supported ERC20 tokens (e.g., wstETH) from the L2 pool to L1. If the token bridge refunds excess ETH to the pool on every call, `bridgeTokens()` will always revert, permanently preventing the bridger from moving token assets to L1. The tokens accumulate in the pool with no viable exit path until the contract is upgraded. Even if the refund is conditional (e.g., only when `msg.value` exceeds the actual fee), the function becomes unreliable and can be bricked by any overpayment.

---

### Likelihood Explanation

Token bridge contracts (e.g., Arbitrum's L2 gateway, Optimism's standard bridge) routinely refund excess native fees to the caller when `msg.value` exceeds the actual messaging cost. The `bridgeTokens()` function is called with an arbitrary `msg.value` supplied by the bridger, making overpayment a realistic and common scenario. The `bridgeTokenToL1{ value: msg.value }` pattern passes the full caller-supplied ETH to the bridge, with no mechanism to ensure exact fee matching. This is the same structural pattern that caused the confirmed High-severity FeeWrapper bug in the referenced report.

---

### Recommendation

Add a `receive()` function to all affected pool contracts so they can accept ETH refunds from external bridge contracts:

```solidity
receive() external payable { }
```

Additionally, after the `bridgeTokenToL1()` call, refund any remaining ETH balance in the pool (attributable to the current call) back to `msg.sender` (the bridger), to avoid stranding ETH in the pool.

---

### Proof of Concept

1. The bridger calls `bridgeTokens(wstETH)` on `RSETHPool` with `msg.value = 0.01 ETH` as the bridge fee.
2. The pool executes:
   ```solidity
   IL2TokenBridge(tokenBridge[wstETH]).bridgeTokenToL1{ value: 0.01 ETH }(l1VaultETHForL2Chain, balance);
   ``` [1](#0-0) 
3. The token bridge uses only `0.008 ETH` as the actual messaging fee and attempts to refund `0.002 ETH` back to the pool contract (its `msg.sender`).
4. The pool has no `receive()` function. The ETH transfer from the bridge to the pool reverts.
5. The entire `bridgeTokens()` transaction reverts. The bridger cannot move wstETH to L1.
6. The same failure occurs on `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge` for the same reason. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPool.sol (L563-568)
```text
        IERC20(token).safeIncreaseAllowance(tokenBridge[token], balance);

        // Call the bridge contract to transfer the tokens (msg.value is included in case we need to pay for additional
        // bridging fees)
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, balance);

```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L493-520)
```text
    /// @dev Bridges tokens to L1 using their corresponding token bridge
    /// @param token The address of the token to bridge
    function bridgeTokens(address token)
        external
        payable
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        if (tokenBridge[token] == address(0)) {
            revert MissingBridgeForToken();
        }

        uint256 balance = getTokenBalanceMinusFees(token);

        if (balance == 0) {
            revert ZeroBridgeAmount();
        }

        // Approve the required amount to the bridge
        IERC20(token).safeIncreaseAllowance(tokenBridge[token], balance);

        // Call the bridge contract to transfer the tokens (msg.value is included in case we need to pay for additional
        // bridging fees)
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, balance);

        emit BridgedTokenToL1(token, l1VaultETHForL2Chain, balance);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L713-740)
```text
    /// @dev Bridges tokens to L1 using their corresponding token bridge
    /// @param token The address of the token to bridge
    function bridgeTokens(address token)
        external
        payable
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        if (tokenBridge[token] == address(0)) {
            revert MissingBridgeForToken();
        }

        uint256 balance = getTokenBalanceMinusFees(token);

        if (balance == 0) {
            revert ZeroBridgeAmount();
        }

        // Approve the required amount to the bridge
        IERC20(token).safeIncreaseAllowance(tokenBridge[token], balance);

        // Call the bridge contract to transfer the tokens (msg.value is included in case we need to pay for additional
        // bridging fees)
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, balance);

        emit BridgedTokenToL1(token, l1VaultETHForL2Chain, balance);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L550-577)
```text
    /// @dev Bridges tokens to L1 using their corresponding token bridge
    /// @param token The address of the token to bridge
    /// @param amount The amount of tokens to bridge
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
    }
```

**File:** contracts/L1Vault.sol (L367-368)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
```

**File:** contracts/L1VaultV2.sol (L562-563)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
```
