Looking at the EVM bridge contracts, I can see the exact analog. Let me verify the key lines.

### Title
ERC-721 Tokens Can Be Permanently Locked in OmniBridge via `initTransfer` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` accepts any arbitrary `tokenAddress` and uses `SafeERC20.safeTransferFrom` to pull tokens into the bridge. Because ERC-721 shares the `transferFrom(address,address,uint256)` selector with ERC-20, the pull succeeds. However, `finTransfer` releases tokens using `SafeERC20.safeTransfer`, which calls `transfer(address,uint256)` — a function that does not exist in the ERC-721 standard. The result is that any ERC-721 token deposited via `initTransfer` is permanently locked in the bridge contract with no on-chain recovery path.

---

### Finding Description

`initTransfer` contains no token-type guard. For any address that is neither a registered bridge token nor a custom minter, it falls through to the bare `safeTransferFrom` path:

```solidity
// OmniBridge.sol lines 406-411
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount
    );
}
``` [1](#0-0) 

OpenZeppelin's `SafeERC20.safeTransferFrom` uses a low-level call encoding `transferFrom(address,address,uint256)`. ERC-721 exposes exactly this selector (it is part of the ERC-721 standard), so the call succeeds and the NFT is transferred into the bridge.

When the user later attempts to redeem on the EVM side, `finTransfer` reaches the symmetric else-branch:

```solidity
// OmniBridge.sol lines 350-354
} else {
    IERC20(payload.tokenAddress).safeTransfer(
        payload.recipient,
        payload.amount
    );
}
``` [2](#0-1) 

`SafeERC20.safeTransfer` encodes `transfer(address,uint256)`. ERC-721 has no such function; the call reverts. The NFT is now irrecoverably locked.

The only existing function that would naturally block ERC-721 is `logMetadata`, which calls `decimals()` — a function absent from ERC-721:

```solidity
// OmniBridge.sol lines 224-227
function logMetadata(address tokenAddress) external payable {
    string memory name = IERC20Metadata(tokenAddress).name();
    string memory symbol = IERC20Metadata(tokenAddress).symbol();
    uint8 decimals = IERC20Metadata(tokenAddress).decimals();
``` [3](#0-2) 

However, `logMetadata` is entirely optional and is never called or checked inside `initTransfer`. There is no on-chain enforcement that a token must pass `logMetadata` before it can be bridged. [4](#0-3) 

---

### Impact Explanation

**Critical — Irreversible fund lock.**

Any ERC-721 token deposited through `initTransfer` is permanently trapped in the `OmniBridge` contract. The `finTransfer` path for that token will always revert, making the redemption path frozen. Recovery requires a contract upgrade by the admin. This matches the allowed impact: *"Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge flows."*

---

### Likelihood Explanation

**Medium.** The entry point is fully public and requires no privilege. A user who mistakes an NFT for a fungible token, or a malicious actor who deliberately wants to grief a specific NFT owner, can trigger this with a single transaction. ERC-721 tokens that also implement `name()` and `symbol()` (which most do) will pass every check in `initTransfer` silently.

---

### Recommendation

Add an ERC-165 interface check inside `initTransfer` before the `safeTransferFrom` fallback path, rejecting any token that advertises the ERC-721 interface (`0x80ac58cd`):

```solidity
// Before the safeTransferFrom fallback:
if (IERC165(tokenAddress).supportsInterface(type(IERC721).interfaceId)) {
    revert ERC721NotSupported();
}
```

Alternatively, require that `IERC20Metadata(tokenAddress).decimals()` succeeds (reverting for standard ERC-721 contracts that do not implement it), mirroring the mitigation suggested in the original Linea report. Either guard must be placed inside `initTransfer` itself, not only in `logMetadata`.

---

### Proof of Concept

1. Deploy a standard ERC-721 contract (e.g., OpenZeppelin `ERC721`). Mint token ID `1` to attacker address `A`.
2. From `A`, call `erc721.approve(omniBridgeAddress, 1)`.
3. From `A`, call `omniBridge.initTransfer(erc721Address, 1, 0, 0, "attacker.near", "")`.
   - `fee (0) < amount (1)` passes the fee check.
   - `customMinters[erc721Address] == address(0)` — falls through.
   - `isBridgeToken[erc721Address] == false` — falls through.
   - `IERC20(erc721Address).safeTransferFrom(A, bridge, 1)` encodes `transferFrom(A, bridge, 1)` — ERC-721 executes this, transferring token ID 1 to the bridge. **Call succeeds.**
4. The `InitTransfer` event is emitted; the NEAR side credits the attacker.
5. Attacker (or anyone) later calls `omniBridge.finTransfer(sig, payload)` with `payload.tokenAddress = erc721Address`, `payload.amount = 1`, `payload.recipient = A`.
   - Falls to the `else` branch: `IERC20(erc721Address).safeTransfer(A, 1)` encodes `transfer(A, 1)`.
   - ERC-721 has no `transfer(address,uint256)` — **call reverts**.
6. Token ID 1 is permanently locked in `OmniBridge`. No on-chain recovery exists without an admin upgrade.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-227)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-354)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-413)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```
