Audit Report

## Title
`bridgeTokenToL1` Lacks Access Control, Permanently Misdirecting Any Caller's Tokens to L1Vault - (File: contracts/bridges/SonicChainNativeTokenBridge.sol)

## Summary

`SonicChainNativeTokenBridge.bridgeTokenToL1` is callable by any external account with no access control, yet the `recipient` parameter is silently ignored for actual token routing due to Sonic gateway's self-claiming constraint. Any user who calls the function directly will have their tokens pulled via `safeTransferFrom`, burned on Sonic L2, and permanently routed to `L1Vault` on L1 — with no on-chain recovery path for the caller.

## Finding Description

The `IL2TokenBridge` interface promises delivery "to the specified recipient on L1." [1](#0-0) 

`SonicChainNativeTokenBridge.bridgeTokenToL1` implements this interface but explicitly documents that `recipient` is informational only: [2](#0-1) 

The function carries no role guard (`external payable override nonReentrant` — no `onlyRole`). The exploit path is:

1. `token.safeTransferFrom(msg.sender, address(this), amount)` pulls tokens from the caller. [3](#0-2) 

2. `sonicBridge.withdraw(uid, originalToken, amount)` burns/locks tokens on Sonic L2, with `bridgeReceiver = address(this)` hardcoded as the sole claimant on L1. [4](#0-3) [5](#0-4) 

3. On L1, `SonicBridgeReceiver.claimAndTransferToVault` (CLAIMER_ROLE only) claims the tokens and forwards them unconditionally to `l1Vault`. [6](#0-5) 

4. `recipient` is used only as entropy in the UID hash and in the emitted event — never for routing. [7](#0-6) 

The non-zero check on `recipient` creates a false expectation of correctness without enforcing it. The protocol's own callers (pool contracts) always pass `l1VaultETHForL2Chain` as recipient, so the intended usage is unaffected — but the function is fully open to external callers. [8](#0-7) 

## Impact Explanation

A user who calls `bridgeTokenToL1(theirL1Address, amount)` directly loses `amount` tokens: they are burned on Sonic L2 and deposited into `L1Vault` on Ethereum. The user receives nothing at `theirL1Address`. Recovery requires a privileged admin to call `SonicBridgeReceiver.emergencyRecover` or `SonicChainNativeTokenBridge.recoverTokens`, with no guaranteed path or timeline. This constitutes **Temporary freezing of funds (Medium)** — funds are inaccessible to the user and require privileged intervention to recover. [9](#0-8) [10](#0-9) 

## Likelihood Explanation

The function is `external` with no role guard, callable by any account on Sonic L2. A user who discovers the `IL2TokenBridge` interface via a block explorer or protocol docs and calls `bridgeTokenToL1` directly — expecting standard interface behavior — will lose their tokens. The non-zero validation on `recipient` reinforces the false expectation. Likelihood is **Low** because most users interact through pool contracts, but the attack surface is fully open and requires no special privileges or preconditions beyond token approval.

## Recommendation

1. **Add access control**: Restrict `bridgeTokenToL1` to authorized pool contracts only (e.g., `onlyRole(BRIDGER_ROLE)`). This matches how pool contracts already gate their own `bridgeTokens` callers.
2. **Alternatively, revert on unexpected recipient**: If the Sonic bridge cannot honor an arbitrary `recipient`, revert with a descriptive error when `recipient != expectedL1VaultAddress` to prevent silent misdirection.
3. **Remove the misleading non-zero check on `recipient`**: If the parameter is truly informational, the validation creates a false expectation and should be removed or replaced with a comment-only acknowledgment.

## Proof of Concept

1. User on Sonic L2 holds `amount` of the bridged token and calls `token.approve(SonicChainNativeTokenBridge, amount)`.
2. User calls `SonicChainNativeTokenBridge.bridgeTokenToL1(userL1Address, amount)`.
3. `token.safeTransferFrom(msg.sender, address(this), amount)` pulls tokens from the user.
4. `sonicBridge.withdraw(uid, originalToken, amount)` burns tokens on Sonic L2, registering `bridgeReceiver` (= `address(this)`) as the sole claimant on L1.
5. Protocol's CLAIMER_ROLE calls `SonicBridgeReceiver.claimAndTransferToVault(...)` → tokens land in `L1Vault`.
6. `userL1Address` receives nothing. The emitted `TokensBridgedToL1(recipient=userL1Address, ...)` event falsely implies the address was honored.
7. **Foundry test**: Deploy mock `ISonicBridge` that burns tokens on `withdraw`. Call `bridgeTokenToL1` as an unprivileged EOA. Assert user balance is zero and bridge/vault received the tokens. Assert `userL1Address` balance is unchanged.

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

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L68-73)
```text
    /// @notice Initiates a withdrawal of a specified amount of tokens to the L1Vault via SonicBridgeReceiver
    /// @dev The recipient parameter is ignored as Sonic gateway only allows contract self-claiming
    /// @dev The actual recipient will be determined by SonicBridgeReceiver which forwards to L1Vault
    /// @param recipient The intended final recipient (informational only - actual recipient is L1Vault)
    /// @param amount The amount of tokens to bridge to L1
    function bridgeTokenToL1(address recipient, uint256 amount) external payable override nonReentrant {
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L83-83)
```text
        token.safeTransferFrom(msg.sender, address(this), amount);
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L115-115)
```text
        sonicBridge.withdraw(uid, originalToken, amount);
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L123-123)
```text
        emit TokensBridgedToL1(recipient, amount, uid, bridgeReceiver);
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L160-173)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert InvalidAmount();

        IERC20(tokenAddress).safeTransfer(recipient, amount);
    }
```

**File:** contracts/bridges/SonicBridgeReceiver.sol (L96-98)
```text
        // Transfer tokens to L1Vault
        IERC20(token).safeTransfer(l1Vault, received);
        emit TokensTransferredToVault(token, received, l1Vault);
```

**File:** contracts/bridges/SonicBridgeReceiver.sol (L164-172)
```text
    function emergencyRecover(address token, address recipient, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(recipient);

        uint256 balance = IERC20(token).balanceOf(address(this));
        uint256 recoverAmount = amount == 0 ? balance : amount;
        if (recoverAmount > balance) revert InsufficientBalance();

        IERC20(token).safeTransfer(recipient, recoverAmount);
    }
```

**File:** contracts/pools/RSETHPool.sol (L567-567)
```text
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, balance);
```
