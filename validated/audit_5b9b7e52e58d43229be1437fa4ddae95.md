### Title
Pre/Post Balance Snapshot in `claimAndTransferToVault` Allows Attacker to Permanently Break Claim and Temporarily Freeze Bridged Tokens - (File: contracts/bridges/SonicBridgeReceiver.sol)

### Summary

`SonicBridgeReceiver.claimAndTransferToVault` uses a pre/post balance snapshot to determine how many tokens to forward to `L1Vault`. Because the Sonic bridge's `EthereumTokenDeposit.claim()` is permissionless (anyone can call it for any withdrawal ID, with tokens always sent to the withdrawal initiator), an attacker can pre-claim a withdrawal on behalf of `SonicBridgeReceiver` before the CLAIMER calls `claimAndTransferToVault`. This causes the subsequent `claimAndTransferToVault` call to always revert, permanently breaking the claim path for that withdrawal ID and temporarily freezing the bridged tokens in the contract.

### Finding Description

`claimAndTransferToVault` in `SonicBridgeReceiver` follows this pattern:

```solidity
// Mark as claimed before external call to prevent reentrancy
claimedWithdrawals[withdrawalId] = true;          // line 82

uint256 balanceBefore = IERC20(token).balanceOf(address(this));  // line 85

ETHEREUM_TOKEN_DEPOSIT.claim(withdrawalId, token, amount, proof); // line 88

uint256 balanceAfter = IERC20(token).balanceOf(address(this));   // line 92
uint256 received = balanceAfter - balanceBefore;                  // line 93
if (received == 0) revert InsufficientBalance();                  // line 94
``` [1](#0-0) 

The `IEthereumTokenDeposit.claim` interface carries no access-control restriction:

```solidity
function claim(uint256 id, address token, uint256 amount, bytes calldata proof) external;
``` [2](#0-1) 

The NatSpec comment on `SonicChainNativeTokenBridge` clarifies the Sonic gateway's design: "Sonic gateway only allows contract self-claiming" means the tokens are always delivered to the withdrawal initiator (`SonicBridgeReceiver`), regardless of who calls `claim`. It does **not** mean only `SonicBridgeReceiver` can call `claim`. [3](#0-2) 

**Attack path:**

1. A withdrawal is initiated on Sonic; the withdrawal ID and merkle proof become public on-chain.
2. Attacker calls `ETHEREUM_TOKEN_DEPOSIT.claim(withdrawalId, token, amount, proof)` directly. Tokens are delivered to `SonicBridgeReceiver` (the withdrawal initiator). `claimedWithdrawals[withdrawalId]` is still `false`.
3. CLAIMER calls `claimAndTransferToVault(withdrawalId, ...)`:
   - `claimedWithdrawals[withdrawalId]` is `false` → passes the guard.
   - `claimedWithdrawals[withdrawalId]` is set to `true`.
   - `balanceBefore` = `amount` (tokens already in contract from step 2).
   - `ETHEREUM_TOKEN_DEPOSIT.claim(...)` is called again → **reverts** because the withdrawal was already claimed on the bridge.
   - The entire transaction reverts, rolling back `claimedWithdrawals[withdrawalId]` to `false`.
4. Every subsequent call to `claimAndTransferToVault` for this `withdrawalId` will always revert at step 3. The tokens are stuck in `SonicBridgeReceiver`.

### Impact Explanation

The bridged tokens are temporarily frozen inside `SonicBridgeReceiver`. They cannot reach `L1Vault` via the normal `claimAndTransferToVault` path. Recovery requires the CLAIMER to call `transferToVault(token, amount)` manually. [4](#0-3) 

Because `transferToVault` exists as a manual recovery path, the freeze is temporary rather than permanent. However, the primary claim mechanism is permanently broken for the attacked withdrawal ID, and the `claimedWithdrawals` accounting is never updated (it stays `false` forever for that ID). This maps to **Medium – Temporary freezing of funds**.

### Likelihood Explanation

The withdrawal ID and merkle proof are fully public on-chain once the Sonic bridge state is finalized. No privileged access is required. Any external caller can execute the attack at zero cost beyond gas. The attacker gains nothing financially, but the attack is trivially executable as a griefing vector against every pending withdrawal.

### Recommendation

Do not use pre/post balance snapshots. Instead, transfer the entire current balance of the token after the claim, or track the expected amount independently:

```solidity
ETHEREUM_TOKEN_DEPOSIT.claim(withdrawalId, token, amount, proof);
uint256 balance = IERC20(token).balanceOf(address(this));
if (balance == 0) revert InsufficientBalance();
IERC20(token).safeTransfer(l1Vault, balance);
```

This mirrors the fix recommended in the reference report: use the entire balance rather than the delta.

### Proof of Concept

1. A user bridges tokens from Sonic; the resulting withdrawal ID `W` and proof `P` are observable on-chain.
2. Attacker calls `ETHEREUM_TOKEN_DEPOSIT.claim(W, token, amount, P)`. Tokens land in `SonicBridgeReceiver`.
3. CLAIMER calls `SonicBridgeReceiver.claimAndTransferToVault(W, token, amount, P)`:
   - `balanceBefore = amount` (already present).
   - Inner `ETHEREUM_TOKEN_DEPOSIT.claim(W, ...)` reverts (already claimed on bridge).
   - Entire tx reverts; `claimedWithdrawals[W]` stays `false`.
4. Step 3 can be repeated indefinitely — it always reverts.
5. Tokens remain in `SonicBridgeReceiver` until an admin/CLAIMER manually calls `transferToVault`. [5](#0-4)

### Citations

**File:** contracts/bridges/SonicBridgeReceiver.sol (L68-99)
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
    }
```

**File:** contracts/bridges/SonicBridgeReceiver.sol (L128-135)
```text
    function transferToVault(address token, uint256 amount) external nonReentrant onlyRole(CLAIMER_ROLE) {
        uint256 balance = IERC20(token).balanceOf(address(this));
        uint256 transferAmount = amount == 0 ? balance : amount;
        if (transferAmount > balance) revert InsufficientBalance();

        IERC20(token).safeTransfer(l1Vault, transferAmount);
        emit TokensTransferredToVault(token, transferAmount, l1Vault);
    }
```

**File:** contracts/interfaces/L2/ISonicBridge.sol (L63-68)
```text
    /// @notice Claims tokens on Ethereum from Sonic withdrawal
    /// @param id The withdrawal ID from Sonic
    /// @param token The token address
    /// @param amount The amount to claim
    /// @param proof The merkle proof for the claim
    function claim(uint256 id, address token, uint256 amount, bytes calldata proof) external;
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L68-70)
```text
    /// @notice Initiates a withdrawal of a specified amount of tokens to the L1Vault via SonicBridgeReceiver
    /// @dev The recipient parameter is ignored as Sonic gateway only allows contract self-claiming
    /// @dev The actual recipient will be determined by SonicBridgeReceiver which forwards to L1Vault
```
