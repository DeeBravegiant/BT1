### Title
`recipient` Parameter Silently Ignored in `bridgeTokenToL1` Causes Funds to Route to L1Vault Instead of Caller-Specified Address - (File: contracts/bridges/SonicChainNativeTokenBridge.sol)

---

### Summary

`SonicChainNativeTokenBridge.bridgeTokenToL1` accepts a `recipient` parameter, validates it, and includes it in event emission and UID generation — but never passes it to the underlying `sonicBridge.withdraw()` call. The Sonic bridge's self-claiming design means all bridged tokens always arrive at `SonicBridgeReceiver` on L1 (which forwards to `L1Vault`), regardless of what `recipient` the caller specified. Since the function has no access control, any external user can call it directly and will lose their tokens to `L1Vault` rather than receiving them at their intended address.

---

### Finding Description

`IL2TokenBridge` explicitly promises:

> "Initiates a withdrawal of a specified amount of tokens to the **specified recipient** on L1" [1](#0-0) 

However, in `SonicChainNativeTokenBridge.bridgeTokenToL1`, the `recipient` parameter is:
- Validated via `UtilLib.checkNonZeroAddress(recipient)` (line 74)
- Included in UID generation (line 96)
- Emitted in the `TokensBridgedToL1` event (line 123)

But it is **never passed** to the actual bridge call:

```solidity
sonicBridge.withdraw(uid, originalToken, amount);
``` [2](#0-1) 

The `ISonicBridge.withdraw()` interface takes no recipient parameter:

```solidity
function withdraw(uint96 uid, address token, uint256 amount) external;
``` [3](#0-2) 

The Sonic bridge's self-claiming design means the contract that calls `withdraw` on L2 is the only one that can claim on L1. Since `bridgeReceiver` is set to `address(this)` in the constructor, all withdrawals are claimable only by `SonicBridgeReceiver` on L1 (same address), which then unconditionally forwards tokens to `L1Vault`: [4](#0-3) [5](#0-4) 

The function carries no access control modifier, making it callable by any external account: [6](#0-5) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns.**

Any external caller who invokes `bridgeTokenToL1` directly with their own address as `recipient` will:
1. Transfer their tokens into the bridge contract.
2. Have those tokens bridged to `SonicBridgeReceiver` on L1 (not to their specified `recipient`).
3. `SonicBridgeReceiver` forwards all claimed tokens to `L1Vault` — a protocol-controlled contract gated by `CLAIMER_ROLE` and `MANAGER_ROLE`.
4. The caller's specified `recipient` receives nothing.
5. The caller's tokens are now inside `L1Vault`, inaccessible without admin intervention.

The tokens are not destroyed, but the caller cannot retrieve them and the promised delivery to `recipient` never occurs. [7](#0-6) 

---

### Likelihood Explanation

**High.** The function is `external` with no access control. The `IL2TokenBridge` interface explicitly documents `recipient` as the L1 destination, creating a strong and reasonable expectation that the parameter is honored. Any integrator or user who reads the interface and calls `bridgeTokenToL1` directly — e.g., to bridge their own tokens to a specific L1 address — will always have their assumption violated. [8](#0-7) 

---

### Recommendation

Two complementary fixes:

1. **Restrict access** — add an `onlyRole(BRIDGER_ROLE)` modifier to `bridgeTokenToL1` so only the authorized pool contract can call it, eliminating the direct-user attack path (consistent with how `RSETHPool.bridgeTokens` already requires `BRIDGER_ROLE`).

2. **Remove or rename the misleading parameter** — since the Sonic bridge's self-claiming design makes it architecturally impossible to honor an arbitrary `recipient`, either remove the parameter entirely (keeping only interface compatibility via a wrapper) or rename it to something like `_informationalRecipient` and add a prominent NatSpec warning that it is not used as the L1 destination. The `IL2TokenBridge` interface NatSpec should also be updated to reflect this constraint. [9](#0-8) 

---

### Proof of Concept

1. Alice holds 100 Sonic-bridged rsETH tokens on Sonic chain.
2. Alice reads `IL2TokenBridge` and sees `bridgeTokenToL1(address recipient, uint256 amount)` — she expects her tokens to arrive at her L1 address.
3. Alice calls `SonicChainNativeTokenBridge.bridgeTokenToL1(aliceL1Address, 100e18)`.
4. The contract validates `aliceL1Address` (non-zero), pulls 100 tokens from Alice, and calls `sonicBridge.withdraw(uid, originalToken, 100e18)` — with no mention of `aliceL1Address`.
5. On L1, `SonicBridgeReceiver.claimAndTransferToVault(...)` is called by the `CLAIMER_ROLE` operator, which transfers the 100 tokens to `L1Vault`.
6. Alice's `aliceL1Address` receives 0 tokens. Alice has no mechanism to recover her funds from `L1Vault` without admin intervention.
7. The emitted `TokensBridgedToL1(aliceL1Address, 100e18, uid, bridgeReceiver)` event falsely implies Alice's address was the recipient. [10](#0-9) [11](#0-10)

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

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L68-124)
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
        }

        emit TokensBridgedToL1(recipient, amount, uid, bridgeReceiver);
    }
```

**File:** contracts/interfaces/L2/ISonicBridge.sol (L13-14)
```text
    /// @param amount The amount to withdraw
    function withdraw(uint96 uid, address token, uint256 amount) external;
```

**File:** contracts/bridges/SonicBridgeReceiver.sol (L68-98)
```text
    function claimAndTransferToVault(
        uint256 withdrawalId,
        address token,
        uint256 amount,
        bytes calldata proof
    )
        external
        nonReentrant
        onlyRole(CLAIMER_ROLE)
    {
        if (withdrawalId == 0) revert InvalidWithdrawalId();
        if (claimedWithdrawals[withdrawalId]) revert WithdrawalAlreadyClaimed();

        // Mark as claimed before external call to prevent reentrancy
        claimedWithdrawals[withdrawalId] = true;

        // Get balance before claim
        uint256 balanceBefore = IERC20(token).balanceOf(address(this));

        // Claim from Sonic bridge
        ETHEREUM_TOKEN_DEPOSIT.claim(withdrawalId, token, amount, proof);
        emit WithdrawalClaimed(withdrawalId, token, amount);

        // Verify we received the tokens and transfer to L1Vault
        uint256 balanceAfter = IERC20(token).balanceOf(address(this));
        uint256 received = balanceAfter - balanceBefore;
        if (received == 0) revert InsufficientBalance();

        // Transfer tokens to L1Vault
        IERC20(token).safeTransfer(l1Vault, received);
        emit TokensTransferredToVault(token, received, l1Vault);
```
