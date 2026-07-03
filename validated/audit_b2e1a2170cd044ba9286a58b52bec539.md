### Title
`SonicChainNativeTokenBridge.bridgeTokenToL1` Silently Ignores `recipient` Parameter, Permanently Misdirecting User Tokens to L1Vault - (File: contracts/bridges/SonicChainNativeTokenBridge.sol)

---

### Summary

`SonicChainNativeTokenBridge` implements the `IL2TokenBridge` interface, which explicitly promises to bridge tokens "to the specified recipient on L1." However, the `recipient` parameter passed to `bridgeTokenToL1` is silently ignored for actual token routing. Tokens are always bridged to `SonicBridgeReceiver` (hardcoded as `address(this)`) and forwarded to `L1Vault`, regardless of what `recipient` is supplied. Because the function has no access control, any user who calls it directly will have their tokens pulled and permanently misdirected to the protocol vault.

---

### Finding Description

The `IL2TokenBridge` interface defines the contract:

> *"Initiates a withdrawal of a specified amount of tokens to the specified recipient on L1"* [1](#0-0) 

`SonicChainNativeTokenBridge.bridgeTokenToL1` implements this interface but explicitly documents that `recipient` is ignored: [2](#0-1) 

The function:
1. Validates `recipient` is non-zero (creating a false expectation that it matters)
2. Pulls `amount` tokens from `msg.sender` via `safeTransferFrom`
3. Calls `sonicBridge.withdraw(uid, originalToken, amount)` — routing always to `bridgeReceiver` (`address(this)`) → `SonicBridgeReceiver` → `L1Vault`
4. Uses `recipient` only as entropy in the UID hash and in the emitted event — never for actual token routing [3](#0-2) 

The `bridgeReceiver` is hardcoded to `address(this)` in the constructor: [4](#0-3) 

Compare this to other `IL2TokenBridge` implementations (`LidoBridge`, `ArbitrumLidoBridge`) which correctly pass `recipient` to the underlying bridge call: [5](#0-4) [6](#0-5) 

The pool contracts that call this bridge always pass `l1VaultETHForL2Chain` as the recipient (which happens to be the correct destination), so the protocol's own usage is unaffected: [7](#0-6) 

However, `bridgeTokenToL1` carries **no access control modifier** — it is callable by any external account.

---

### Impact Explanation

Any user who directly calls `SonicChainNativeTokenBridge.bridgeTokenToL1(theirAddress, amount)`:

- Has `amount` tokens pulled from their wallet via `safeTransferFrom(msg.sender, address(this), amount)`
- Receives nothing on L1 at `theirAddress`
- Their tokens are permanently routed to `L1Vault` via `SonicBridgeReceiver`

The user suffers a permanent loss of funds with no on-chain mechanism for recovery. The `recipient` parameter is validated (non-zero check) and emitted in the event, creating a false assurance that it controls the destination.

**Impact: Medium — Temporary freezing of funds** (tokens are locked in `L1Vault`; recovery requires privileged admin action with no guaranteed path).

---

### Likelihood Explanation

The function is `external` with no role guard. A user on Sonic L2 who discovers the `IL2TokenBridge` interface (e.g., via block explorer, protocol docs, or a frontend that exposes bridge contracts) and calls `bridgeTokenToL1` directly — expecting the standard interface behavior — will lose their tokens. The non-zero validation on `recipient` reinforces the false expectation. Likelihood is **Low** because most users interact through pool contracts, but the attack surface is fully open.

---

### Recommendation

1. **Add access control**: Restrict `bridgeTokenToL1` to only be callable by authorized pool contracts (e.g., `onlyRole(BRIDGER_ROLE)` or a whitelist of pool addresses). This matches how all pool contracts already gate their `bridgeTokens` callers.
2. **Alternatively, revert on unexpected recipient**: If the Sonic bridge mechanism cannot honor an arbitrary `recipient`, revert with a descriptive error when `recipient != l1VaultAddress` to prevent silent misdirection.
3. **Remove the misleading non-zero check on `recipient`**: If the parameter is truly informational only, the validation creates a false expectation of correctness.

---

### Proof of Concept

1. User on Sonic L2 holds `amount` of the bridged token and approves `SonicChainNativeTokenBridge` to spend it.
2. User calls:
   ```solidity
   SonicChainNativeTokenBridge.bridgeTokenToL1(userAddressOnL1, amount)
   ```
3. `token.safeTransferFrom(msg.sender, address(this), amount)` pulls tokens from the user.
4. `sonicBridge.withdraw(uid, originalToken, amount)` routes tokens to `bridgeReceiver` = `address(this)` (SonicBridgeReceiver), which forwards to `L1Vault`.
5. `userAddressOnL1` receives nothing. Tokens are permanently in `L1Vault`. The emitted `TokensBridgedToL1(recipient=userAddressOnL1, ...)` event falsely implies the user's address was honored. [8](#0-7)

### Citations

**File:** contracts/interfaces/L2/IL2TokenBridge.sol (L11-15)
```text
     * @notice Initiates a withdrawal of a specified amount of tokens to the specified recipient on L1
     * @param recipient The address of the recipient on L1
     * @param amount The amount of tokens to bridge to L1
     */
    function bridgeTokenToL1(address recipient, uint256 amount) external payable;
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L57-57)
```text
        bridgeReceiver = address(this);
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L68-120)
```text
    /// @notice Initiates a withdrawal of a specified amount of tokens to the L1Vault via SonicBridgeReceiver
    /// @dev The recipient parameter is ignored as Sonic gateway only allows contract self-claiming
    /// @dev The actual recipient will be determined by SonicBridgeReceiver which forwards to L1Vault
    /// @param recipient The intended final recipient (informational only - actual recipient is L1Vault)
    /// @param amount The amount of tokens to bridge to L1
    function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
        UtilLib.checkNonZeroAddress(recipient);

        // recipient parameter is kept for IL2TokenBridge interface compatibility
        // but the actual flow will be: SonicBridge -> SonicBridgeReceiver -> L1Vault
        if (amount == 0) revert InvalidAmount();

        // No additional msg.value is needed for the fees
        if (msg.value != 0) revert NoMsgValueNeeded();

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
```

**File:** contracts/bridges/LidoBridge.sol (L73-75)
```text
        // Bridge wstETH to the L1 recipient
        lidoBridge.withdrawTo(address(wstETH), recipient, amount, 0, bytes(""));

```

**File:** contracts/bridges/ArbitrumLidoBridge.sol (L79-82)
```text
        // Bridge wstETH to the L1 recipient
        arbitrumL2GatewayRouter.outboundTransfer(address(wstETHOnL1), recipient, amount, bytes(""));

        emit WstETHBridged(recipient, amount);
```

**File:** contracts/pools/RSETHPool.sol (L563-568)
```text
        IERC20(token).safeIncreaseAllowance(tokenBridge[token], balance);

        // Call the bridge contract to transfer the tokens (msg.value is included in case we need to pay for additional
        // bridging fees)
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, balance);

```
