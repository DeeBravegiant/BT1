### Title
`SonicChainNativeTokenBridge::bridgeTokenToL1` strict balance-difference equality check permanently DoSes bridging for rebasing tokens — (`contracts/bridges/SonicChainNativeTokenBridge.sol`)

### Summary

`SonicChainNativeTokenBridge.bridgeTokenToL1` uses a strict equality check (`balanceBefore - balanceAfter != amount`) to verify that the Sonic bridge consumed exactly `amount` tokens. For rebasing tokens (e.g., stETH or any token whose `transfer` delivers ±1–2 wei due to share-based rounding), this check will always revert, permanently blocking the bridge path for those tokens.

### Finding Description

`bridgeTokenToL1` contains two problematic assumptions about exact token amounts:

**Issue 1 — Deposit side (lines 83–86):** [1](#0-0) 

After `safeTransferFrom(msg.sender, address(this), amount)`, the code checks `balance < amount`. For a rebasing token, the contract may receive only `amount - 1` or `amount - 2` wei. If the contract held zero balance before the call, `balance` equals the actual received amount, which is less than `amount`, causing an `InsufficientBalance` revert.

**Issue 2 — Withdrawal side (lines 108–121):** [2](#0-1) 

`safeIncreaseAllowance(address(sonicBridge), amount)` approves exactly `amount`, but the contract may only hold `amount - 1` tokens (received in step 1). The Sonic bridge's `withdraw()` call will either fail on the pull, or succeed but consume `amount - 1` tokens. In the latter case, `balanceBefore - balanceAfter = amount - 1 ≠ amount`, triggering `BridgeFailed`.

This is structurally identical to the ERC5115Form withdrawal bug: a strict `!=` equality on a balance delta that is off by ≤2 wei for rebasing tokens.

The bridge is generic — its `token` is set at construction time and validated only via `tokenPairs.mintedToOriginal()`. The `RSETHPoolV3WithNativeChainBridge` pool supports multiple tokens and routes each through its registered bridge: [3](#0-2) 

Any rebasing token added to the pool with `SonicChainNativeTokenBridge` as its bridge will be permanently unbridge-able.

### Impact Explanation

**Medium — Temporary (effectively permanent) freezing of funds.**

Users who deposit a rebasing token into `RSETHPoolV3WithNativeChainBridge` on Sonic cannot bridge it back to L1. Every call to `bridgeTokenToL1` reverts. Tokens accumulate in the pool with no exit path until the bridge contract is replaced (requires admin action). Individual transactions revert atomically so no funds are directly stolen, but the bridging functionality is completely broken for the affected token.

### Likelihood Explanation

The `SonicChainNativeTokenBridge` is deployed on Sonic and is currently wired to `RsETHTokenWrapper` (non-rebasing). However:
- The contract is explicitly designed as a generic `IL2TokenBridge` implementation.
- `RSETHPoolV3WithNativeChainBridge.addSupportedToken()` allows adding new tokens with any bridge address, including this one.
- The Sonic deployment already lists `RSETHPoolV3WithNativeChainBridge` as a live contract. [4](#0-3) 

If a rebasing token (e.g., a Sonic-bridged stETH) is added with this bridge, the DoS is immediate and requires no attacker — any ordinary user calling `bridgeTokenToL1` triggers it.

### Recommendation

**For the withdrawal check (line 119):** Replace the strict equality with a tolerance, analogous to the ERC5115Form fix:

```solidity
uint256 EPSILON = 2; // wei tolerance for rebasing tokens
if (balanceBefore - balanceAfter + EPSILON < amount) {
    revert BridgeFailed();
}
```

**For the deposit check (line 86):** Measure the actual received amount using a balance-difference pattern instead of comparing against the nominal `amount`:

```solidity
uint256 balanceBefore = token.balanceOf(address(this));
token.safeTransferFrom(msg.sender, address(this), amount);
uint256 received = token.balanceOf(address(this)) - balanceBefore;
// use `received` instead of `amount` for subsequent allowance and bridge calls
```

### Proof of Concept

1. Deploy `SonicChainNativeTokenBridge` with a Sonic-bridged stETH as `_token` (valid per `tokenPairs.mintedToOriginal`).
2. Register it in `RSETHPoolV3WithNativeChainBridge` via `addSupportedToken`.
3. A user deposits stETH into the pool on Sonic.
4. The BRIDGER calls `bridgeTokens(stETH, amount)`, which calls `bridgeTokenToL1(l1Vault, amount)`.
5. `safeTransferFrom` delivers `amount - 1` wei (stETH rounding).
6. `balance = amount - 1 < amount` → `InsufficientBalance` revert (Issue 1), **or** if the contract had residual balance, `sonicBridge.withdraw(uid, originalToken, amount)` burns `amount - 1`, making `balanceBefore - balanceAfter = amount - 1 ≠ amount` → `BridgeFailed` revert (Issue 2).
7. All bridging attempts revert; stETH is permanently stranded in the pool on Sonic.

### Citations

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L83-86)
```text
        token.safeTransferFrom(msg.sender, address(this), amount);

        uint256 balance = token.balanceOf(address(this));
        if (balance < amount) revert InsufficientBalance();
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L108-121)
```text
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L570-574)
```text
        // Approve the required amount to the bridge
        IERC20(token).safeIncreaseAllowance(tokenBridge[token], amount);

        // Call the bridge contract to transfer the tokens
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, amount);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L595-601)
```text
    /// @dev Adds a supported token
    /// @param token The token address
    /// @param oracle The oracle address
    /// @param bridge The bridge address
    function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
        _addSupportedToken(token, oracle, bridge);
    }
```
